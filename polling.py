"""Adaptive start-to-start polling budgets with durable per-cycle evidence."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS polling_events (
 id uuid PRIMARY KEY, stream_url text NOT NULL, recorded_at timestamptz NOT NULL,
 observation_id uuid, details jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS polling_events_stream_time ON polling_events(stream_url,recorded_at DESC);
"""


def advance(state, elapsed, base, maximum=60, stable_samples=12, outcome='analyzed',
            appearance_pending=False):
    if not math.isfinite(elapsed) or elapsed<0 or not 0<base<=maximum or stable_samples<1:
        raise ValueError('Invalid backpressure inputs')
    budget=min(maximum,max(base,float(state.get('effective_interval_seconds',base))))
    stable=int(state.get('stable_samples',0))
    action='hold'
    if outcome in ('error','database_unavailable'):
        target=max(budget*1.5,elapsed*1.25)
        stable=0
        action='failure_backoff'
    elif elapsed>budget:
        target=max(budget*1.25,elapsed*1.25)
        stable=0
        action='increase'
    elif appearance_pending and outcome == 'analyzed':
        target=min(budget,max(base,elapsed*1.25))
        stable=0
        action='follow_appearance_candidate'
    else:
        target=budget
        if outcome in ('outside_active_hours','skipped_already_running'):
            stable=0
        elif elapsed<=budget*.7:
            stable+=1
            if stable>=stable_samples:
                target=max(base,budget*.9)
                stable=0
                action='recover' if target<budget else 'hold'
        else:
            stable=0
    target=min(maximum,max(base,math.ceil(target*10)/10))
    if elapsed>=maximum:
        action='saturated'
    return {'effective_interval_seconds':target,'stable_samples':stable,
            'base_interval_seconds':base,'maximum_interval_seconds':maximum,
            'cycle_seconds':elapsed,'previous_interval_seconds':budget,'action':action,
            'outcome':outcome, 'stable_samples_required':stable_samples,
            'appearance_pending':appearance_pending}


def wait_seconds(state, elapsed):
    # At the ceiling, leave a whole cooldown rather than immediately retrying.
    if state['action']=='saturated':
        return state['effective_interval_seconds']
    return max(0,state['effective_interval_seconds']-elapsed)


def folder(cfg):
    return Path(cfg['data'])/'polling'


def local_state_path(cfg):
    key=hashlib.sha256(cfg['stream'].encode()).hexdigest()
    return folder(cfg)/(key+'-state.json')


def load(cfg):
    candidates=[]
    path=local_state_path(cfg)
    if path.exists():
        candidates.append(json.loads(path.read_text()))
    try:
        import psycopg
        with psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=5) as conn:
            if conn.execute("SELECT to_regclass('polling_events')").fetchone()[0]:
                row=conn.execute('SELECT details FROM polling_events WHERE stream_url=%s '
                                 'ORDER BY recorded_at DESC LIMIT 1',(cfg['stream'],)).fetchone()
                if row:
                    candidates.append(row[0])
    except Exception:
        pass  # Local state is the durable fallback while PostgreSQL is offline.
    if not candidates:
        return {}
    latest=max(candidates,key=lambda event:event['recorded_at'])
    state=latest['state']
    old_base=state.get('base_interval_seconds',cfg.get('interval'))
    if (old_base != cfg.get('interval',old_base)
            and state.get('effective_interval_seconds',old_base)<=old_base):
        return {}  # A manual base change should not retain the old unpressured base.
    return state


def record(cfg, state, observation_id=None, import_events=True):
    """Queue before import; recovery preserves timing history idempotently."""
    event={'id':str(uuid.uuid4()),'stream_url':cfg['stream'],
           'recorded_at':datetime.now(timezone.utc).isoformat(),
           'observation_id':observation_id,'state':state}
    pending=folder(cfg)/'pending'
    pending.mkdir(parents=True,exist_ok=True)
    def atomic(path,value):
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(value)+'\n')
        temp.replace(path)
    atomic(local_state_path(cfg),event)
    atomic(pending/(event['id']+'.json'),event)
    if import_events:
        result=flush(cfg)
        return dict(result,event=event)
    return {'event':event,'queued':True}


def flush(cfg):
    pending=folder(cfg)/'pending'
    pending.mkdir(parents=True,exist_ok=True)
    if not any(pending.glob('*.json')):
        return {'queued':False}
    try:
        import psycopg
        with psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=5) as conn:
            from psycopg.types.json import Jsonb
            # Serialize replay so concurrent processes cannot move the same files.
            conn.execute('SELECT pg_advisory_lock(hashtext(%s))',('polling:'+str(folder(cfg).resolve()),))
            queued=sorted(((path,json.loads(path.read_text())) for path in pending.glob('*.json')),
                          key=lambda item:item[1]['recorded_at'])
            paths=[path for path,_ in queued]
            for path,saved in queued:
                conn.execute('INSERT INTO polling_events VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                             (saved['id'],saved['stream_url'],saved['recorded_at'],saved['observation_id'],Jsonb(saved)))
                if saved['observation_id']:
                    conn.execute("UPDATE observations SET details=details || %s WHERE id=%s",
                                 (Jsonb({'polling':saved['state']}),saved['observation_id']))
            conn.commit()
            for path in paths:
                path.unlink(missing_ok=True)
    except Exception as exc:
        return {'queued':True,'error':type(exc).__name__}
    return {'queued':False}
