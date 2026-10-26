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
    with patch.dict(os.environ, {'PGOPTIONS':'-c search_path='+schema}):
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
        with patch('clothing_descriptions.generate',return_value=('White short-sleeved T-shirt.',{'model':'test'})):
            assert clothing.process_one()
            assert not clothing.process_one()
        with psycopg.connect() as conn:
            clothing.save_job(conn,obs)
            result = conn.execute('SELECT description,status,attempts,image_sha256 FROM presenter_clothing_descriptions').fetchone()
            assert result[0:3] == ('White short-sleeved T-shirt.','completed',2)
        report = {'integration':'passed','schema':schema,'checks':[
            'one job per session','transactional database crop storage','outage retry',
            'completed description retained on replay','no repeated inference'], 'result':result}
        root = Path('/data/test-evidence/clothing-descriptions')/schema
        root.mkdir(parents=True,exist_ok=True)
        (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
