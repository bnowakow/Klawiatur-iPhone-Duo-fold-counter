"""Isolated PostgreSQL checks; Google writes are mocked throughout."""
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql

import sheets_jobs as jobs
import sheets_export as export


def main():
    schema = 'sheets_jobs_test_' + uuid.uuid4().hex
    key = 'sheets-jobs-test:' + schema
    with psycopg.connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ, {'PGOPTIONS': '-c search_path='+schema}), patch.object(jobs, 'JOB_LOCK', key):
        with jobs.connect() as conn:
            jobs.initialize(conn)
            conn.execute("UPDATE sheets_export_jobs SET next_hourly_at=date_trunc('hour',now())+interval '1 hour'")
            old_deadline = jobs.state(conn)['next_hourly_at']
            jobs.initialize(conn)
            migrated = jobs.state(conn)['next_hourly_at']
            assert (migrated-old_deadline).total_seconds()==60
            jobs.initialize(conn)
            assert jobs.state(conn)['next_hourly_at']==migrated
            conn.execute('CREATE TABLE presenter_sessions '
                         '(id uuid PRIMARY KEY, stream_url text, ended_at timestamptz, version_at timestamptz)')
        with jobs.connect() as holder, patch.object(export, 'sync') as write:
            assert jobs.lock(holder, key)
            assert jobs.sync_once()['status']=='skipped'
            write.assert_not_called()
        with patch.object(export, 'snapshot', return_value=[]), \
             patch.object(jobs.auth, 'locked', side_effect=nullcontext), \
             patch.object(jobs.auth, 'token', return_value='fixture'), \
             patch.object(export, 'sync', return_value={'synced': True}) as write:
            assert jobs.sync_once()['synced']
            with jobs.connect() as conn:
                first = jobs.state(conn)
                at = datetime.now(timezone.utc)
                assert first['last_success_at'] and first['next_hourly_at'].minute==1
                assert jobs.due(first, jobs.closed_fingerprint(conn), at) is None
                conn.execute('INSERT INTO presenter_sessions VALUES (%s,%s,%s,%s)',
                             (str(uuid.uuid4()), export.STREAM, at, at))
                assert jobs.due(first, jobs.closed_fingerprint(conn), at)=='recorded_finish'
            assert jobs.sync_once()['synced']
            with jobs.connect() as conn:
                second = jobs.state(conn)
                assert second['next_hourly_at']==first['next_hourly_at']
            write.side_effect = export.ExportError('fixture outage')
            try:
                jobs.sync_once()
                raise AssertionError('Expected failure')
            except export.ExportError:
                pass
            with jobs.connect() as conn:
                failed = jobs.state(conn)
                assert failed['last_success_at']==second['last_success_at']
                assert failed['next_retry_at']>datetime.now(timezone.utc)
                assert jobs.due(failed, 'changed', datetime.now(timezone.utc)) is None
        # A killed child must release its session advisory lock before a new job.
        child = "import os,time,psycopg; c=psycopg.connect(autocommit=True); " \
                "assert c.execute('SELECT pg_try_advisory_lock(hashtext(%s))',(os.environ['SHEETS_TEST_LOCK'],)).fetchone()[0]; " \
                "print('locked',flush=True); time.sleep(30)"
        try:
            subprocess.run([sys.executable, '-c', child], capture_output=True,
                           env=dict(os.environ, SHEETS_TEST_LOCK=key), timeout=2)
            raise AssertionError('Expected timeout')
        except subprocess.TimeoutExpired as exc:
            assert b'locked' in exc.stdout
        with jobs.connect() as conn:
            assert jobs.lock(conn, key), 'Timed-out job leaked the lock'
    with psycopg.connect() as conn:
        conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
    print(json.dumps({'integration': 'passed', 'checks': ['mutual exclusion', 'finish trigger',
          'hourly deadline preserved', 'failure retry', 'timeout releases lock']}))


if __name__ == '__main__':
    main()
