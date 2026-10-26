from datetime import datetime, timedelta, timezone
import subprocess
import unittest
from unittest.mock import MagicMock, patch

import sheets_export as export
import sheets_jobs as jobs


class SheetsJobsTests(unittest.TestCase):
    def test_hourly_schedule_runs_at_one_minute_past(self):
        at = datetime(2026, 10, 26, 8, 45, tzinfo=timezone.utc)
        self.assertEqual(jobs.next_hour(at), at.replace(hour=9, minute=1))
        self.assertEqual(jobs.next_hour(at.replace(hour=9, minute=0)), at.replace(hour=9, minute=1))
        self.assertEqual(jobs.next_hour(at.replace(hour=9, minute=1)), at.replace(hour=10, minute=1))

    def test_finish_and_hourly_triggers_and_retry_delay(self):
        at = datetime.now(timezone.utc)
        state = dict(last_success_at=at, next_hourly_at=at+timedelta(minutes=30),
                     closed_fingerprint='old', next_retry_at=None)
        self.assertIsNone(jobs.due(state, 'old', at))
        self.assertEqual(jobs.due(state, 'new', at), 'recorded_finish')
        self.assertEqual(jobs.due(state, 'old', at+timedelta(minutes=30)), 'hourly')
        self.assertEqual(jobs.due(dict(state, last_success_at=None), 'old', at), 'startup')
        self.assertIsNone(jobs.due(dict(state, next_retry_at=at+timedelta(seconds=60)), 'new', at))

    def test_job_contention_skips_before_google_or_schema_mutations(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = [False]
        with patch.object(jobs, 'connect') as connect, patch.object(export, 'sync') as sync:
            connect.return_value.__enter__.return_value = conn
            self.assertEqual(jobs.sync_once(), {'status': 'skipped', 'reason': 'another_sync_running'})
        self.assertEqual(conn.execute.call_count, 1)
        sync.assert_not_called()

    def test_manual_and_scheduled_jobs_share_a_three_minute_subprocess_limit(self):
        with patch.object(jobs.subprocess, 'run', return_value=MagicMock(returncode=0, stdout='{"synced":true}')) as run:
            self.assertEqual(jobs.run_job(), {'synced': True})
        self.assertEqual(run.call_args.kwargs['timeout'], 180)
        self.assertEqual(run.call_args.args[0][-1], '_sync')

    def test_timeout_persists_retry_and_does_not_retry_inline(self):
        with patch.object(jobs.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sync', 180)) as run, \
             patch.object(jobs, 'connect'), patch.object(jobs, 'initialize'), patch.object(jobs, 'failed') as failed:
            with self.assertRaisesRegex(export.ExportError, '180 seconds'):
                jobs.run_job()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(failed.call_args.args[1], 'job_timeout')


if __name__ == '__main__':
    unittest.main()
