import json
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

    def capture(self, n, anomaly=False, stream='live', prune=True):
        identifier = str(uuid.UUID(int=n + 1))
        self.ids.append(identifier)
        folder = self.root / 'frames' / identifier
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'frame.jpg').write_bytes(b'image')
        obs = {'id': identifier, 'stream_url': stream, 'status': 'analyzed',
               'counter_status': 'rejected' if anomaly else 'accepted', 'details': {}}
        retention.register(self.root, obs)
        if prune:
            retention.prune(self.root)
        return obs

    def exists(self, n):
        return (self.root / 'frames' / str(uuid.UUID(int=n + 1))).exists()

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
