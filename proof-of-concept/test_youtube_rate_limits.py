from datetime import datetime,timezone
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import youtube_rate_limits as limits


class PostingLimitTests(unittest.TestCase):
    def test_retry_after_and_redacted_provider_errors(self):
        now=datetime(2026,10,26,6,tzinfo=timezone.utc)
        self.assertEqual(limits.retry_seconds('120',now),120)
        self.assertEqual(limits.retry_seconds('Mon, 26 Oct 2026 06:05:00 GMT',now),300)
        self.assertIsNone(limits.retry_seconds('NaN',now))
        data=limits.provider_error({'error':{'message':'Bearer private-token', 'errors':[{'reason':'rateLimitExceeded','domain':'youtube.liveChat'}]}},'120',now)
        self.assertEqual(data['reason'],'rateLimitExceeded')
        self.assertEqual(data['retry_after_seconds'],120)
        self.assertNotIn('private-token',data['provider_message'])


if __name__=='__main__':
    unittest.main()
