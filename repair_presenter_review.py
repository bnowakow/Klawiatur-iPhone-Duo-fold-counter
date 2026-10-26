"""Preview/apply a human-confirmed resolution of false appearance-change cues."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import uuid
from presenter_sessions import counter_sample,save_sessions

SCHEMA="""
CREATE TABLE IF NOT EXISTS presenter_review_resolutions (
 id uuid PRIMARY KEY,stream_url text NOT NULL,session_id uuid NOT NULL,
 resolved_at timestamptz NOT NULL,reason text NOT NULL,details jsonb NOT NULL
);
"""


def resolve(conn,stream,session_id,reason,apply=False,data_dir=None):
    from psycopg.types.json import Jsonb
    if not reason.strip():raise ValueError('A human review reason is required')
    if apply:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(stream,))
        if data_dir and any((Path(data_dir)/'outbox').glob('*.json')):
            raise ValueError('Replay pending observations before resolving attribution')
    row=conn.execute('SELECT state FROM monitor_state WHERE stream_url=%s',(stream,)).fetchone()
    if not row:raise ValueError('Stream state was not found')
    before=row[0]
    state=json.loads(json.dumps(before))
    current=state.get('presenters',{}).get('current',{})
    if current.get('id')!=str(session_id) or current.get('ended_at'):
        raise ValueError('Expected stint is no longer current; review the new boundary separately')
    appearances=state.setdefault('appearances',{})
    if not appearances.get('attribution_review_required') and not appearances.get('candidate') and not state['presenters'].get('candidate'):
        raise ValueError('There is no pending appearance review to resolve')
    frozen_at=current.get('last_counter_at')
    if not frozen_at:raise ValueError('The current stint has no trusted last reading')
    end=state['last_observed_at']
    observations=[r[0] for r in conn.execute('SELECT to_jsonb(o) FROM observations o WHERE stream_url=%s '
        "AND observed_at>%s AND observed_at<=%s AND status='analyzed' ORDER BY observed_at,id",(stream,frozen_at,end)).fetchall()]
    if not observations:raise ValueError('No observations are available for the reviewed interval')
    if any(o['details'].get('presenter_session_id') not in (None,str(session_id)) or o['clothing_status']=='night_break_detected' for o in observations):
        raise ValueError('The interval includes another stint or night break; refusing to merge it')
    old_counter=current['last_counter']
    for observation in observations:
        counter_sample(current,observation)
    now=datetime.now(timezone.utc).isoformat()
    current['version_at']=now
    state['presenters'].pop('candidate',None)
    state['appearances'].pop('candidate',None)
    state['appearances'].pop('attribution_review_required',None)
    for view in state.get('clothing',{}).get('views',{}).values():
        view.pop('tracking',None)
    aid=str(uuid.uuid4())
    state['appearances']['last_review_resolution']=aid
    appearance_rows=[r[0] for r in conn.execute('SELECT to_jsonb(a) FROM presenter_appearances a WHERE stream_url=%s '
        'AND detected_at>%s AND detected_at<=%s',(stream,frozen_at,end)).fetchall()]
    summary={'applied':apply,'audit_id':aid,'session_id':str(session_id),'reviewed_from':frozen_at,
             'reviewed_through':end,'old_counter':old_counter,'restored_counter':current['last_counter'],
             'observations_reviewed':len(observations),'appearance_cues_resolved':len(appearance_rows),'reason':reason}
    if not apply:return summary
    conn.execute(SCHEMA)
    conn.execute('INSERT INTO presenter_review_resolutions VALUES (%s,%s,%s,%s,%s,%s)',
                 (aid,stream,session_id,now,reason,Jsonb({'summary':summary,'before_state':before,
                   'before_observations':observations,'before_appearances':appearance_rows})))
    save_sessions(conn,{'details':{'presenter_session_updates':[current]}})
    conn.execute('UPDATE monitor_state SET state=%s,updated_at=now() WHERE stream_url=%s',(Jsonb(state),stream))
    annotation=Jsonb({'presenter_review_resolution':aid,'reviewed_same_presenter':True})
    conn.execute('UPDATE observations SET details=details || %s WHERE id=ANY(%s::uuid[])',
                 (annotation,[o['id'] for o in observations]))
    for row in appearance_rows:
        conn.execute("UPDATE presenter_appearances SET status='resolved_same_presenter',version_at=%s,details=details || %s WHERE id=%s",
                     (now,Jsonb({'presenter_review_resolution':aid,'reviewed_same_presenter':True,
                                 'status':'resolved_same_presenter','previous_status':row['status']}),row['id']))
    return summary


def main():
    import psycopg
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',type=uuid.UUID,required=True)
    parser.add_argument('--reason',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    with psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=10) as conn:
        result=resolve(conn,os.environ['STREAM_URL'],args.session,args.reason,args.apply,os.getenv('DATA_DIR','/data'))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
