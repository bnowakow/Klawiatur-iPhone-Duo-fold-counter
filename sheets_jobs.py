"""Hourly and recorded-finish Sheets syncs, serialized across all callers."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import sheets_export as export
import sheets_auth as auth

JOB_TIMEOUT_SECONDS = 180
POLL_SECONDS = 10
DESTINATION = f'{export.SPREADSHEET_ID}:{export.TAB_ID}'
JOB_LOCK = 'sheets-sync:' + DESTINATION
WORKER_LOCK = 'sheets-worker:' + DESTINATION
SCHEMA = """
CREATE TABLE IF NOT EXISTS sheets_export_jobs (
 destination text PRIMARY KEY,
 last_success_at timestamptz, next_hourly_at timestamptz,
 closed_fingerprint text, last_attempt_at timestamptz,
 last_error text, failures integer NOT NULL DEFAULT 0,
 next_retry_at timestamptz
);
"""


def connect():
    import psycopg
    return psycopg.connect(os.getenv('DATABASE_URL', ''), connect_timeout=10, autocommit=True)


def lock(conn, key):
    return conn.execute('SELECT pg_try_advisory_lock(hashtext(%s))', (key,)).fetchone()[0]


def initialize(conn):
    conn.execute(SCHEMA)
    conn.execute('INSERT INTO sheets_export_jobs(destination) VALUES (%s) ON CONFLICT DO NOTHING', (DESTINATION,))
    # Migrate an existing full-hour deadline once; retries/checkpoints survive.
    conn.execute("UPDATE sheets_export_jobs SET next_hourly_at=next_hourly_at+interval '1 minute' "
                 "WHERE destination=%s AND next_hourly_at=date_trunc('hour',next_hourly_at)", (DESTINATION,))


def closed_fingerprint(conn):
    rows = conn.execute('SELECT id,ended_at,version_at FROM presenter_sessions '
                        'WHERE stream_url=%s AND ended_at IS NOT NULL ORDER BY id', (export.STREAM,)).fetchall()
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


def next_hour(at):
    candidate = datetime.fromtimestamp((int(at.timestamp())//3600)*3600+60, timezone.utc)
    return candidate if candidate > at else candidate+timedelta(hours=1)


def state(conn):
    row = conn.execute('SELECT last_success_at,next_hourly_at,closed_fingerprint,next_retry_at '
                       'FROM sheets_export_jobs WHERE destination=%s', (DESTINATION,)).fetchone()
    return dict(zip(('last_success_at', 'next_hourly_at', 'closed_fingerprint', 'next_retry_at'), row))


def due(current, fingerprint, at):
    if current['next_retry_at'] and at < current['next_retry_at']:
        return None
    if current['last_success_at'] is None:
        return 'startup'
    if current['closed_fingerprint'] != fingerprint:
        return 'recorded_finish'
    if current['next_hourly_at'] is None or at >= current['next_hourly_at']:
        return 'hourly'
    return None


def failed(conn, error):
    conn.execute("UPDATE sheets_export_jobs SET last_error=%s,failures=failures+1,"
                 "next_retry_at=now()+make_interval(secs=>LEAST(900,60*power(2,LEAST(failures,4)))::int) "
                 "WHERE destination=%s", (error, DESTINATION))


def sync_once():
    # The session lock covers DB snapshot, OAuth refresh and Google write. It is
    # automatically released on process exit, including a timeout kill.
    with connect() as conn:
        if not lock(conn, JOB_LOCK):
            return {'status': 'skipped', 'reason': 'another_sync_running'}
        initialize(conn)
        conn.execute('UPDATE sheets_export_jobs SET last_attempt_at=now() WHERE destination=%s', (DESTINATION,))
        try:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                rows = export.snapshot(conn)
                fingerprint = closed_fingerprint(conn)
            with auth.locked():
                result = export.sync(auth.token(os.environ['YOUTUBE_PROJECT_ID']), rows)
            at = datetime.now(timezone.utc)
            # A finish-triggered sync does not push the next :01 deadline back.
            conn.execute('UPDATE sheets_export_jobs SET last_success_at=%s,closed_fingerprint=%s,'
                         'next_hourly_at=CASE WHEN next_hourly_at IS NULL OR next_hourly_at<=%s '
                         'THEN %s ELSE next_hourly_at END,last_error=NULL,failures=0,next_retry_at=NULL '
                         'WHERE destination=%s', (at, fingerprint, at, next_hour(at), DESTINATION))
            return result
        except Exception as exc:
            failed(conn, type(exc).__name__)
            raise


def run_job(timeout_seconds=JOB_TIMEOUT_SECONDS):
    """Bound the whole job, including waiting for the separate OAuth file lock."""
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '_sync'],
                                capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        # subprocess.run kills and reaps the child before raising. Its DB lock
        # and file lock are released; persist the retry delay in a fresh session.
        with connect() as conn:
            initialize(conn)
            failed(conn, 'job_timeout')
        raise export.ExportError(f'Sheets sync exceeded {timeout_seconds} seconds and was stopped') from None
    if result.returncode:
        raise export.ExportError('Sheets sync failed; inspect make sheets-job-status and make sheets-job-logs')
    return json.loads(result.stdout)


def run_worker():
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    with connect() as conn:
        if not lock(conn, WORKER_LOCK):
            print(json.dumps({'event': 'sheets_worker_already_running'}), flush=True)
            return
        initialize(conn)
        print(json.dumps({'event': 'sheets_worker_started', 'poll_seconds': POLL_SECONDS,
                          'job_timeout_seconds': JOB_TIMEOUT_SECONDS}), flush=True)
        while not stopped.is_set():
            current = state(conn)
            reason = due(current, closed_fingerprint(conn), datetime.now(timezone.utc))
            if reason:
                try:
                    result = run_job()
                    print(json.dumps({'event': 'sheets_sync', 'trigger': reason, 'result': result}), flush=True)
                except export.ExportError as exc:
                    print(json.dumps({'event': 'sheets_sync_failed', 'trigger': reason, 'error': str(exc)}), flush=True)
            stopped.wait(POLL_SECONDS)


def job_status():
    with connect() as conn:
        initialize(conn)
        row = conn.execute('SELECT last_success_at,next_hourly_at,last_attempt_at,last_error,failures,next_retry_at '
                           'FROM sheets_export_jobs WHERE destination=%s', (DESTINATION,)).fetchone()
        return dict(zip(('last_success_at', 'next_hourly_at', 'last_attempt_at', 'last_error', 'failures', 'next_retry_at'), row))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', '_sync', 'status'])
    args = parser.parse_args()
    try:
        if args.command == 'run':
            run_worker()
        else:
            result = sync_once() if args.command == '_sync' else job_status()
            print(json.dumps(result, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({'event': 'sheets_job_error', 'error_type': type(exc).__name__}), file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
