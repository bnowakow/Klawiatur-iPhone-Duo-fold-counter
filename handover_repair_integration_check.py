"""Verify reviewed handover repair in an isolated PostgreSQL schema."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

import supervisor
from compare_current_tester import SCHEMA as COMPARISON_SCHEMA
from presenter_sessions import new_session, counter_sample
from repair_presenter_handover import repair


def main():
    schema = 'handover_repair_test_'+uuid.uuid4().hex
    with psycopg.connect(os.getenv('DATABASE_URL','')) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ, {'PGOPTIONS': '-c search_path='+schema}), tempfile.TemporaryDirectory() as temp:
        cfg = dict(supervisor.config(), stream='integration:'+schema, data=Path(temp))
        base = datetime.now(timezone.utc)-timedelta(minutes=10)
        observations = []
        frames = Path('/app/proof-of-concept/test-data/2026-10-26-dark-clothing-handover')
        for seconds, value in [(0,1000),(60,1100),(90,1104),(120,1120),(150,1140)]:
            obs = supervisor.observation(cfg)
            obs.update(observed_at=(base+timedelta(seconds=seconds)).isoformat(), status='analyzed',
                       raw_counter=value,accepted_counter=value,counter_status='accepted',
                       clothing_status='no_clothing_change_detected',
                       frame_path=str(frames/('last-old-main.jpg' if seconds<90 else 'first-new-main.jpg')))
            obs['details']['camera_view']={'view':'presenter_main'}
            observations.append(obs)
        current = new_session(observations[0],observations[0]['observed_at'],'first_observed')
        current['confirmed_at']=current['detected_at']
        for obs in observations:
            counter_sample(current,obs)
            obs['details']['presenter_session_id']=current['id']
        observations[-1]['details']['presenter_session_updates']=[current]
        with psycopg.connect(os.getenv('DATABASE_URL','')) as conn:
            supervisor.ensure_schema(conn)
            conn.execute(COMPARISON_SCHEMA)
            for obs in observations: supervisor.insert(conn,obs)
            conn.execute('INSERT INTO monitor_state(stream_url,state) VALUES (%s,%s)',
                         (cfg['stream'],Jsonb({'presenters':{'current':current}})))
            conn.execute('INSERT INTO tester_comparisons VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                         (uuid.uuid4(),datetime.now(timezone.utc),cfg['stream'],uuid.uuid4(),current['id'],
                          'compared',40,45,44,5,4,Jsonb({})))
            conn.commit()
            ids=(uuid.UUID(current['id']),uuid.UUID(observations[1]['id']),uuid.UUID(observations[2]['id']))
            preview=repair(conn,cfg,*ids)
            assert preview['applied'] is False
            assert conn.execute('SELECT count(*) FROM presenter_sessions').fetchone()[0]==1
            result=repair(conn,cfg,*ids,apply=True)
            conn.commit()
            rows=conn.execute('SELECT last_counter,ended_at,baseline_counter FROM presenter_sessions '
                              'ORDER BY detected_at').fetchall()
            assert len(rows)==2 and rows[0][0]==1100 and rows[1][0]==1140 and rows[1][2]==1104,rows
            assert rows[0][1].isoformat()==observations[1]['observed_at']
            detail=conn.execute('SELECT details FROM observations WHERE id=%s', (observations[2]['id'],)).fetchone()[0]
            assert detail['previous_presenter_session_id']==current['id']
            assert detail['presenter_session_id']==result['new_session_id']
            journal=conn.execute('SELECT details FROM presenter_handover_corrections').fetchone()[0]
            assert journal['before']['monitor_state']['presenters']['current']['last_counter']==1140
            assert journal['before']['observations'][0]['details']['presenter_session_id']==current['id']
            assert journal['before']['comparisons'][0]['stint_fpm']==44
            compared=conn.execute('SELECT status,stint_fpm FROM tester_comparisons').fetchone()
            assert compared==('superseded_by_handover_correction',None),compared
            state=conn.execute('SELECT state FROM monitor_state').fetchone()[0]
            assert state['presenters']['current']['id']==result['new_session_id']
            assert state['clothing']['views']['presenter_main']['garment_baselines']
            try:
                repair(conn,cfg,*ids,apply=True)
                raise AssertionError('Repeated correction must not create another stint')
            except ValueError:
                pass
        root=Path('/data/test-evidence/handover-repair')/schema
        root.mkdir(parents=True)
        report={'integration':'passed','schema':schema,'checks':[
            'preview does not change stints','corrected boundary preserves unassigned folds',
            'original state and attribution snapshots retained','mixed comparisons invalidated',
            'current stint and garment baseline replaced','duplicate repair refused']}
        (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))


if __name__=='__main__': main()
