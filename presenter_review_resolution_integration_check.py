"""Audited same-presenter resolution in an isolated PostgreSQL schema."""
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import uuid
from unittest.mock import patch
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import supervisor
from presenter_sessions import new_session,counter_sample
from repair_presenter_review import resolve


def main():
    schema='review_resolution_test_'+uuid.uuid4().hex
    with psycopg.connect(os.getenv('DATABASE_URL','')) as c:
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ,{'PGOPTIONS':'-c search_path='+schema}):
        cfg=dict(supervisor.config(),stream='integration:'+schema)
        base=datetime.now(timezone.utc)-timedelta(minutes=3)
        observations=[]
        for seconds,count in [(0,1000),(30,1020),(60,1040),(90,1060)]:
            obs=supervisor.observation(cfg)
            obs.update(observed_at=(base+timedelta(seconds=seconds)).isoformat(),status='analyzed',
                       accepted_counter=count,clothing_status='no_clothing_change_detected')
            observations.append(obs)
        current=new_session(observations[0],observations[0]['observed_at'],'first_observed')
        current['confirmed_at']=current['detected_at']
        for obs in observations[:2]:counter_sample(current,obs)
        current['version_at']=observations[-1]['observed_at']
        for obs in observations:
            obs['details'].update(presenter_session_id=current['id'],presenter_attribution_review_required=True)
        observations[-1]['details']['presenter_session_updates']=[current]
        with psycopg.connect(os.getenv('DATABASE_URL','')) as c:
            supervisor.ensure_schema(c)
            for obs in observations:supervisor.insert(c,obs)
            state={'presenters':{'current':current,'candidate':{'id':'unconfirmed'}},
                   'appearances':{'attribution_review_required':True},'last_observed_at':observations[-1]['observed_at']}
            c.execute('INSERT INTO monitor_state(stream_url,state) VALUES (%s,%s)',(cfg['stream'],Jsonb(state)))
            c.commit()
            preview=resolve(c,cfg['stream'],current['id'],'Fixture human confirmed unchanged')
            assert not preview['applied'] and preview['restored_counter']==1060
            assert c.execute('SELECT last_counter FROM presenter_sessions').fetchone()[0]==1020
            result=resolve(c,cfg['stream'],current['id'],'Fixture human confirmed unchanged',True)
            c.commit()
            assert c.execute('SELECT count(*) FROM presenter_sessions').fetchone()[0]==1
            assert c.execute('SELECT last_counter,baseline_counter FROM presenter_sessions').fetchone()==(1060,1000)
            audit=c.execute('SELECT details FROM presenter_review_resolutions').fetchone()[0]
            assert audit['before_state']['appearances']['attribution_review_required']
            assert audit['before_observations'][0]['details']['presenter_attribution_review_required']
            saved=c.execute('SELECT state FROM monitor_state').fetchone()[0]
            assert 'candidate' not in saved['presenters'] and not saved['appearances'].get('attribution_review_required')
            try:
                resolve(c,cfg['stream'],current['id'],'Repeated fixture',True)
                raise AssertionError('Repeated resolution accepted')
            except ValueError:pass
    folder=Path('/data/test-evidence/presenter-review-resolution')/schema
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':True,'schema':schema,'result':result},indent=2))


if __name__=='__main__':
    main()
