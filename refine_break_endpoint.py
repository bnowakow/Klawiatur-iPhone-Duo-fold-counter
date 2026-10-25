"""Auditable correction of a late estimated break endpoint using its own evidence."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import supervisor


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',type=uuid.UUID,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    cfg=supervisor.config()
    with psycopg.connect(row_factory=dict_row) as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(cfg['stream'],))
        row=conn.execute('SELECT s.*,e.details AS end_evidence FROM presenter_sessions s '
                         'JOIN presenter_session_ends e ON e.session_id=s.id '
                         'WHERE s.id=%s AND s.stream_url=%s FOR UPDATE OF s,e',
                         (args.session,cfg['stream'])).fetchone()
        assert row and row['end_reason']=='night_break_sign_counter_stopped_review_required'
        assert row['end_evidence']['break_scene_verified'] is True
        stop=row['last_counter_at']
        assert stop and row['detected_at'] <= stop <= row['ended_at']
        source=conn.execute('SELECT id FROM observations WHERE stream_url=%s AND observed_at=%s '
                            'AND accepted_counter=%s ORDER BY id LIMIT 1',
                            (cfg['stream'],stop,row['last_counter'])).fetchone()
        assert source,'A real attributed observation must support the corrected estimate'
        report={'session_id':str(args.session),'before':row['ended_at'].isoformat(),
                'after':stop.isoformat(),'source_observation_id':str(source['id']),
                'basis':'last_attributed_counter_observation','applied':False}
        if args.apply and stop < row['ended_at']:
            now=datetime.now(timezone.utc)
            evidence=dict(row['end_evidence'])
            evidence.update(previous_estimated_stop_at=row['ended_at'].isoformat(),
                            estimated_stop_at=stop.isoformat(),estimated_stop_basis=report['basis'],
                            source_observation_id=report['source_observation_id'],refined_at=now.isoformat(),
                            superseded_last_increase_at=evidence.get('last_increase_at'),last_increase_at=None)
            conn.execute('UPDATE presenter_sessions SET ended_at=%s,version_at=%s WHERE id=%s',
                         (stop,now,args.session))
            conn.execute('UPDATE presenter_session_ends SET estimated_stopped_at=%s,details=%s WHERE session_id=%s',
                         (stop,Jsonb(evidence),args.session))
            saved=conn.execute('SELECT state FROM monitor_state WHERE stream_url=%s',(cfg['stream'],)).fetchone()
            state=saved['state']
            closed=state.get('presenters',{}).get('night_closed',{})
            if closed.get('session_id')==str(args.session):
                closed['estimated_stop_at']=stop.isoformat()
                conn.execute('UPDATE monitor_state SET state=%s,updated_at=%s WHERE stream_url=%s',
                             (Jsonb(state),now,cfg['stream']))
            report.update(applied=True,refined_at=now.isoformat())
            audit=supervisor.observation(cfg)
            audit.update(observed_at=now.isoformat(),status='night_break_endpoint_refined',
                         details={'endpoint_refinement':report})
            supervisor.insert(conn,audit)
            report['audit_observation_id']=audit['id']
    root=cfg['data']/'test-evidence/endpoint-refinements'/str(uuid.uuid4())
    root.mkdir(parents=True)
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    main()
