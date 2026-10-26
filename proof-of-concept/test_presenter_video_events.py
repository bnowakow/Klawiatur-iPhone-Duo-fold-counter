from datetime import datetime, timezone
import unittest

from presenter_video_events import bookmark, capture_metadata


class VideoEventTests(unittest.TestCase):
    def test_live_start_produces_estimated_offset_and_review_preroll(self):
        start = datetime(2026, 10, 26, 5, tzinfo=timezone.utc)
        meta = capture_metadata({'id': 'vaiHWiExDXg', 'release_timestamp': start.timestamp(),
                                 'url': 'signed-secret'})
        self.assertNotIn('url', meta)
        result = bookmark('2026-10-26T07:00:26+01:00', meta)
        self.assertEqual(result['video_offset_seconds'], 3626)
        self.assertEqual(result['review_url'], 'https://www.youtube.com/watch?v=vaiHWiExDXg&t=3596s')
        self.assertEqual(result['timing_quality'], 'estimated_from_capture_clock')

    def test_missing_live_start_does_not_use_upload_time_or_invent_zero(self):
        meta = capture_metadata({'id': 'vaiHWiExDXg', 'timestamp': 1700000000,
                                 'upload_date': '20200101'})
        result = bookmark('2026-10-26T06:00:26+00:00', meta)
        self.assertEqual(result['timing_quality'], 'unavailable')
        self.assertIsNone(result['video_offset_seconds'])
        self.assertIsNone(result['review_url'])

    def test_invalid_future_or_malformed_metadata_does_not_create_false_links(self):
        for value in [None, True, 'date', float('nan'), float('inf'), -1, 1e50]:
            self.assertIsNone(capture_metadata({'release_timestamp': value})['broadcast_started_at'])
        timing = {'video_id': 'vaiHWiExDXg', 'broadcast_started_at': '2026-10-26T06:00:00+00:00'}
        self.assertIsNone(bookmark('2026-10-26T05:00:00+00:00', timing)['video_offset_seconds'])
        self.assertIn('&t=0s', bookmark('2026-10-26T06:00:10+00:00', timing)['review_url'])
        timing['video_id'] = '../other?secret'
        self.assertIsNone(bookmark('2026-10-26T07:00:00+00:00', timing)['review_url'])
