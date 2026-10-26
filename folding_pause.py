"""Preview/apply a human-confirmed daytime folding pause, with an audit backup."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from presenter_sessions import save_sessions

SCHEMA = """
CREATE TABLE IF NOT EXISTS folding_pause_resolutions (
 id uuid PRIMARY KEY, stream_url text NOT NULL, session_id uuid NOT NULL,
 confirmed_at timestamptz NOT NULL, kind text NOT NULL, reason text NOT NULL,
 details jsonb NOT NULL
);
"""


def confirm_pause(conn, stream, session_id, reason, apply=False, data_dir=None):
    from psycopg.types.json import Jsonb
    if not reason.strip():
        raise ValueError('Human confirmation and a reason are required')
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (stream,))
    if apply and data_dir and any((Path(data_dir)/'outbox').glob('*.json')):
        raise ValueError('Replay queued observations before confirming a pause')
    row = conn.execute('SELECT state FROM monitor_state WHERE stream_url=%s', (stream,)).fetchone()
    if not row:
        raise ValueError('Stream not found')
    before = row[0]
    state = json.loads(json.dumps(before))
    presenters = state.setdefault('presenters', {})
    current = presenters.get('current')
    if not current or current['id'] != str(session_id) or current.get('ended_at'):
        raise ValueError('Expected session is no longer current')
    stopped = current.get('last_increase_at')
    if not stopped or not current.get('folding_started_at'):
        raise ValueError('No trusted folding endpoint; review the session manually')
    value = current['last_counter']
    source = conn.execute("SELECT to_jsonb(o) FROM observations o WHERE stream_url=%s "
                          "AND observed_at=%s AND accepted_counter=%s", (stream, stopped, value)).fetchone()
    if not source or not current['folding_started_at'] <= stopped <= current['last_counter_at']:
        raise ValueError('Last increase does not establish the final counter')
    later = conn.execute('SELECT 1 FROM observations WHERE stream_url=%s AND observed_at>%s '
                         'AND accepted_counter>%s LIMIT 1', (stream, stopped, value)).fetchone()
    if later:
        raise ValueError('Later folding evidence exists; refusing an obsolete pause')
    latest = conn.execute('SELECT to_jsonb(o) FROM observations o WHERE stream_url=%s '
                          'ORDER BY observed_at DESC LIMIT 1', (stream,)).fetchone()[0]
    if latest['observed_at'] != datetime.fromisoformat(state['last_observed_at']).isoformat():
        # PostgreSQL JSON may use a different timezone spelling: compare instants.
        if datetime.fromisoformat(latest['observed_at']) != datetime.fromisoformat(state['last_observed_at']):
            raise ValueError('Latest observation and saved state differ; replay before applying')
    now = datetime.now(timezone.utc).isoformat()
    audit = str(uuid.uuid4())
    evidence = {'confirmed_at': now, 'observation_id': latest['id'],
                'estimated_stop_at': stopped, 'estimated_stop_basis': 'last_accepted_counter_increase',
                'stop_lower_bound': current.get('last_increase_lower_bound'),
                'source_observation_id': source[0]['id'], 'final_counter': value,
                'kind': 'freeze_test', 'human_confirmed': True, 'reason': reason,
                'exact_stop_verified': False, 'audit_id': audit}
    current.update(ended_at=stopped, end_reason='confirmed_freeze_test_pause',
                   last_counter_at=stopped, version_at=now, end_evidence=evidence)
    presenters['activity_pause'] = dict(evidence, session_id=current['id'], counter=value)
    for key in ('current', 'candidate', 'night_activity', 'night_closed', 'pause_resume_candidate'):
        presenters.pop(key, None)
    # The audit retains all original uncertainty; it belongs to the closed stint.
    state['appearances'] = {'last_pause_resolution': audit}
    summary = dict(applied=apply, audit_id=audit, session_id=str(session_id),
                   estimated_stopped_at=stopped, counter=value,
                   folds=value-current['baseline_counter'], kind='freeze_test')
    if apply:
        conn.execute(SCHEMA)
        conn.execute('INSERT INTO folding_pause_resolutions VALUES (%s,%s,%s,%s,%s,%s,%s)',
                     (audit, stream, session_id, now, 'freeze_test', reason,
                      Jsonb({'before_state': before, 'summary': summary,
                             'source_observation': source[0], 'confirmation_observation': latest})))
        save_sessions(conn, {'details': {'presenter_session_updates': [current]}})
        conn.execute('UPDATE monitor_state SET state=%s,updated_at=now() WHERE stream_url=%s', (Jsonb(state), stream))
    return summary


def main():
    import psycopg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True, type=uuid.UUID)
    parser.add_argument('--reason', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with psycopg.connect(os.getenv('DATABASE_URL', ''), connect_timeout=10) as conn:
        result = confirm_pause(conn, os.environ['STREAM_URL'], args.session, args.reason,
                               args.apply, os.getenv('DATA_DIR', '/data'))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
