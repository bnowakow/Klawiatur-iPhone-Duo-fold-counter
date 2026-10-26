import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
import uuid

import frame_retention as retention


class FrameRetentionTests(unittest.TestCase):
    def test_stint_start_first_increase_and_finish_protect_review_frames(self):
        for session in [{'first_observation_id': 'frame'},
                        {'folding_start_observation_id': 'frame'},
                        {'ended_at': '2026-10-26T06:59:32+00:00'}]:
            obs = {'id': 'frame', 'details': {'presenter_session_updates': [session]}}
            self.assertIn('presenter_video_boundary', retention.reasons(obs))
        self.assertNotIn('presenter_video_boundary', retention.reasons(
            {'id': 'later', 'details': {'presenter_session_updates': [{'first_observation_id': 'frame'}]}}))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ids = []

    def capture(self, n, anomaly=False, stream='live', prune=True, observed_at='2020-01-01T00:00:00+00:00'):
        identifier = str(uuid.UUID(int=n + 1))
        self.ids.append(identifier)
        folder = self.root / 'frames' / identifier
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'frame.jpg').write_bytes(b'image')
        obs = {'id': identifier, 'stream_url': stream, 'status': 'analyzed',
               'observed_at': observed_at,
               'counter_status': 'rejected' if anomaly else 'accepted', 'details': {}}
        retention.register(self.root, obs)
        if prune:
            retention.prune(self.root)
        return obs

    def exists(self, n):
        return (self.root / 'frames' / str(uuid.UUID(int=n + 1))).exists()

    def test_two_hour_window_then_original_anomaly_policy(self):
        now = datetime(2026, 10, 26, 7, tzinfo=timezone.utc)
        for n in range(50):
            self.capture(n, anomaly=n == 15, prune=False,
                         observed_at=(now-timedelta(minutes=90)).isoformat())
        retention.prune(self.root, now=now)
        self.assertTrue(all(self.exists(n) for n in range(50)))
        # Reopening the ledger preserves timestamps; age, not capture count, expires history.
        retention.prune(self.root, now=now+timedelta(minutes=31))
        self.assertEqual([n for n in range(50) if self.exists(n)],
                         list(range(5,26))+list(range(40,50)))

    def test_exact_two_hour_boundary_and_delayed_observation(self):
        now = datetime(2026, 10, 26, 7, tzinfo=timezone.utc)
        for n in range(15):
            self.capture(n, prune=False, observed_at=(now-timedelta(hours=2)).isoformat())
        retention.prune(self.root, now=now)
        self.assertTrue(self.exists(0))
        retention.prune(self.root, now=now+timedelta(microseconds=1))
        self.assertFalse(self.exists(0))
        self.assertTrue(self.exists(14))

    def test_migrates_legacy_ledger_using_observation_time(self):
        import sqlite3
        conn = sqlite3.connect(self.root / 'frame-retention.sqlite3')
        conn.execute('CREATE TABLE captures (seq INTEGER PRIMARY KEY AUTOINCREMENT, '
                     'id TEXT UNIQUE NOT NULL,stream TEXT NOT NULL,reasons TEXT NOT NULL,retained INTEGER NOT NULL)')
        conn.close()
        obs = self.capture(0, prune=False, observed_at='2026-10-26T07:00:00+00:00')
        with retention.connect(self.root) as conn:
            conn.execute('UPDATE captures SET captured_at=NULL')
        retention.register(self.root, obs)
        with retention.connect(self.root) as conn:
            self.assertEqual(conn.execute('SELECT captured_at FROM captures').fetchone()[0],
                             datetime(2026,10,26,7,tzinfo=timezone.utc).timestamp())

    def test_legacy_missing_timestamp_uses_image_time_without_resetting_age(self):
        import os
        now = datetime(2026,10,26,7,tzinfo=timezone.utc)
        for n in range(15):
            self.capture(n, prune=False)
            os.utime(self.root / 'frames' / self.ids[-1] / 'frame.jpg',
                     (now.timestamp(),now.timestamp()))
        with retention.connect(self.root) as conn:
            conn.execute('UPDATE captures SET captured_at=NULL')
        retention.prune(self.root, now=now+timedelta(hours=1))
        self.assertTrue(self.exists(0))
        retention.prune(self.root, now=now+timedelta(hours=3))
        self.assertFalse(self.exists(0))

    def test_image_cap_overrides_recent_debug_window(self):
        now = datetime.now(timezone.utc)
        self.capture(0, observed_at=now.isoformat())
        retention.prune(self.root, now=now)
        self.assertTrue(self.exists(0))
        with self.assertLogs(level='ERROR'):
            report=retention.enforce_image_limit(self.root, limit=1)
        self.assertLessEqual(report['after_bytes'],1)
        self.assertFalse((self.root / 'frames' / self.ids[0] / 'frame.jpg').exists())

    def test_exact_ten_before_and_after(self):
        for n in range(50):
            self.capture(n, anomaly=n == 15)
        kept = [n for n in range(50) if self.exists(n)]
        self.assertEqual(kept, list(range(5, 26)) + list(range(40, 50)))

    def test_normal_capture_buffer_is_bounded(self):
        for n in range(40):
            self.capture(n)
        self.assertEqual([n for n in range(40) if self.exists(n)], list(range(30, 40)))

    def test_overlap_and_restart(self):
        for n in range(65):
            # Every call reopens the durable ledger, as a restart would.
            self.capture(n, anomaly=n in {15, 22})
        self.assertEqual([n for n in range(65) if self.exists(n)],
                         list(range(5, 33)) + list(range(55, 65)))

    def test_retry_does_not_consume_context(self):
        obs = self.capture(0, anomaly=True)
        for _ in range(30):
            retention.register(self.root, obs)
        for n in range(1, 30):
            self.capture(n)
        self.assertTrue(self.exists(10))
        self.assertFalse(self.exists(11))

    def test_post_insert_outage_promotes_existing_capture(self):
        for n in range(12):
            obs = self.capture(n, prune=n < 11)
        obs['details']['database_error'] = 'outage'
        retention.register(self.root, obs)
        for n in range(12, 40):
            self.capture(n)
        self.assertTrue(self.exists(1))
        self.assertTrue(self.exists(21))
        self.assertFalse(self.exists(22))

    def test_streams_do_not_share_windows(self):
        for n in range(80):
            self.capture(n, anomaly=n == 20, stream='a' if n % 2 == 0 else 'b')
        self.assertTrue(self.exists(0))  # ten preceding captures of stream a
        self.assertTrue(self.exists(40)) # ten following captures of stream a
        self.assertFalse(self.exists(1))
        self.assertFalse(self.exists(42))

    def test_backfill_before_cleanup(self):
        for n in range(50):
            self.capture(n, anomaly=n == 20, prune=False)
        retention.prune(self.root)
        self.assertEqual([n for n in range(50) if self.exists(n)],
                         list(range(10, 31)) + list(range(40, 50)))

    def test_unclassified_and_regression_files_untouched(self):
        unknown = self.root / 'frames' / str(uuid.uuid4())
        unknown.mkdir(parents=True)
        (unknown / 'frame.jpg').write_bytes(b'unknown')
        fixture = self.root / 'test-evidence' / 'frame.jpg'
        fixture.parent.mkdir()
        fixture.write_bytes(b'fixture')
        for n in range(25):
            self.capture(n)
        self.assertTrue((unknown / 'frame.jpg').exists())
        self.assertTrue(fixture.exists())

    def test_anomaly_definition(self):
        for status in ['rejected', 'suspect_slow', 'unreadable']:
            self.assertTrue(retention.reasons({'counter_status': status}))
        for status in ['accepted', 'accepted_paused', 'accepted_rebased', 'provisional']:
            self.assertFalse(retention.reasons({'counter_status': status}))
        for status in ['change_pending', 'camera_view_unknown_review_required',
                       'possible_handover_review_required']:
            self.assertTrue(retention.reasons({'clothing_status': status}))
        self.assertTrue(retention.reasons({'status':'duplicate_frame'}))
        self.assertTrue(retention.reasons({'details': {'database_error':'outage'}}))


if __name__ == '__main__':
    unittest.main()
