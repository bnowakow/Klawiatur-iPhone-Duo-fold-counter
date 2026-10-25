"""Verify scheduler telemetry, replay and schema lock lifetime in isolation."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql

import polling
import supervisor


def main():
    schema='polling_test_'+uuid.uuid4().hex
    with psycopg.connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ,{'PGOPTIONS':'-c search_path='+schema}):
        with psycopg.connect() as conn:
            supervisor.ensure_schema(conn)
            assert conn.info.transaction_status==psycopg.pq.TransactionStatus.IDLE
            with psycopg.connect() as other:
                assert other.execute("SELECT pg_try_advisory_xact_lock(hashtext('folding-schema-setup'))").fetchone()[0]
        with tempfile.TemporaryDirectory() as temp:
            cfg=supervisor.config()
            cfg.update(data=Path(temp),stream='polling:'+str(uuid.uuid4()))
            with redirect_stdout(io.StringIO()):
                obs=supervisor.cycle(cfg,Path('/fixtures/latest/live-frame.jpg'))
            assert obs['status']=='analyzed',obs
            state=polling.advance({},7,4)
            with patch('psycopg.connect',side_effect=psycopg.OperationalError('simulated outage')):
                assert polling.record(cfg,state,obs['id'])['queued']
                assert polling.load(cfg)==state
            stable=polling.advance(state,1,4)
            assert not polling.record(cfg,stable,obs['id'])['queued']
            assert polling.load(cfg)==stable
            assert not list((Path(temp)/'polling/pending').glob('*.json'))
            with psycopg.connect() as conn:
                assert conn.execute('SELECT count(*) FROM polling_events WHERE stream_url=%s',(cfg['stream'],)).fetchone()[0]==2
                details=conn.execute('SELECT details FROM observations WHERE id=%s',(obs['id'],)).fetchone()[0]
                assert details['polling']==stable
            report={'integration':'passed','schema':schema,'state':stable,
                    'checks':['schema commit before capture','schema lock released','queued outage evidence',
                              'restart recovery','database events','observation timing link']}
            root=Path(os.getenv('POLLING_TEST_OUTPUT_DIR','/data/test-evidence/backpressure'))/schema
            root.mkdir(parents=True,exist_ok=True)
            (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps({'integration':'passed','schema':schema,'evidence_path':str(root)}))


if __name__=='__main__':
    main()
