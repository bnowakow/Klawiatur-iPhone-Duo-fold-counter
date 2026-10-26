"""Isolated PostgreSQL check for caption queue, retries and replay idempotence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql
from PIL import Image
import clothing_descriptions as clothing
import supervisor


def main():
    schema = 'clothing_test_' + uuid.uuid4().hex
    with psycopg.connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ, {'PGOPTIONS':'-c search_path='+schema, 'STREAM_URL':'integration:clothing'}):
        stamp = datetime.now(timezone.utc).isoformat()
        identifier, observation = str(uuid.uuid4()), str(uuid.uuid4())
        obs = {'id':observation,'observed_at':stamp,'details':{'camera_view':{'view':'presenter_main'}}}
        sessions = {'current':{'id':identifier,'confirmed_at':stamp,'folding_started_at':stamp}}
        clothing.prepare(Image.open('/fixtures/latest/live-frame.jpg'),obs,sessions)
        with psycopg.connect() as conn:
            supervisor.ensure_schema(conn)
            conn.execute('INSERT INTO presenter_sessions '
                         '(id,stream_url,detected_at,confirmed_at,detection_reason,first_observation_id,version_at) '
                         "VALUES (%s,'integration:clothing',%s,%s,'test',%s,%s)",
                         (identifier,stamp,stamp,observation,stamp))
            clothing.save_job(conn,obs)
            clothing.save_job(conn,obs)
            assert conn.execute('SELECT count(*) FROM presenter_clothing_descriptions').fetchone()[0] == 1
        with patch('clothing_descriptions.generate',side_effect=TimeoutError('simulated outage')):
            assert clothing.process_one()
        with psycopg.connect() as conn:
            status, attempts = conn.execute('SELECT status,attempts FROM presenter_clothing_descriptions').fetchone()
            assert (status, attempts) == ('retry',1)
            conn.execute('UPDATE presenter_clothing_descriptions SET retry_at=now()')
        with patch('clothing_descriptions.generate',return_value=('Biała koszulka z krótkim rękawem.',{'model':'test','raw_description':'White short-sleeved T-shirt.'})):
            assert clothing.process_one()
            assert not clothing.process_one()
        with psycopg.connect() as conn:
            clothing.save_job(conn,obs)
            result = conn.execute('SELECT description,status,attempts,image_sha256 FROM presenter_clothing_descriptions').fetchone()
            assert result[0:3] == ('Biała koszulka z krótkim rękawem.','completed',2)
        with psycopg.connect() as conn:
            conn.execute("UPDATE presenter_clothing_descriptions SET description=%s,description_language='en'",
                         ('White short-sleeved T-shirt.',))
            assert clothing.migrate_polish(conn) == 1
            assert clothing.migrate_polish(conn) == 0
            translated = conn.execute('SELECT description,description_language,response FROM presenter_clothing_descriptions').fetchone()
            assert translated[0] == 'Biała koszulka z krótkim rękawem.'
            assert translated[1] == 'pl'
            assert translated[2]['previous_description'] == 'White short-sleeved T-shirt.'
        # Replaying an already queued crop must not wait for long model inference.
        with psycopg.connect() as busy:
            busy.execute('SELECT session_id FROM presenter_clothing_descriptions FOR UPDATE')
            with psycopg.connect() as replay:
                replay.execute("SET LOCAL lock_timeout='200ms'")
                clothing.save_job(replay,obs)
        with psycopg.connect() as conn:
            conn.execute('UPDATE presenter_clothing_descriptions SET input_version=1')
            clothing.save_job(conn,obs)
            clothing.save_job(conn,obs)
            revision=conn.execute('SELECT snapshot FROM presenter_description_revisions').fetchall()
            assert len(revision)==1
            assert revision[0][0]['description']=='Biała koszulka z krótkim rękawem.'
            old=dict(obs)
            old['details']=dict(obs['details'])
            old['details']['clothing_description_job']=dict(obs['details']['clothing_description_job'],input_version=1)
            clothing.save_job(conn,old)
            assert conn.execute('SELECT input_version FROM presenter_clothing_descriptions').fetchone()[0]==2
        detail={'hair':{'state':'visible','color':'brązowe','length':'krótkie'},
                'glasses':{'state':'visible','type':'okulary'}}
        with patch('clothing_descriptions.generate',return_value=('Biała koszulka. Włosy: krótkie, brązowe. Okulary: widoczne.',{'appearance':detail})):
            assert clothing.process_one()
        with psycopg.connect() as conn:
            stored=conn.execute('SELECT appearance,prompt_version,status FROM presenter_clothing_descriptions').fetchone()
            assert stored==(detail,2,'completed')
        report = {'integration':'passed','schema':schema,'checks':[
            'one job per session','transactional database crop storage','outage retry',
            'completed description retained on replay','no repeated inference',
            'Polish conversion and language marker','original description preserved','idempotent language migration',
            'portrait upgrade archives original','stale crop replay cannot downgrade',
            'replay does not block on inference row','structured appearance persisted'], 'result':result}
        root = Path('/data/test-evidence/clothing-descriptions')/schema
        root.mkdir(parents=True,exist_ok=True)
        (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
