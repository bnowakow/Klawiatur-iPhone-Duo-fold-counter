from contextlib import contextmanager
from pathlib import Path
import sys,unittest
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import youtube_quota as quota


class AdaptivePolicyHookTests(unittest.TestCase):
    def test_only_posting_rate_rejections_trigger_policy(self):
        for method,reason,called in [('liveChatMessages.insert','rateLimitExceeded',True),
                                     ('liveChatMessages.insert','forbidden',False),
                                     ('videos.list','rateLimitExceeded',False)]:
            with self.subTest(method=method,reason=reason),patch.object(quota,'connect') as connect, \
                 patch.object(quota.rate_limits,'record_result'),patch.object(quota.rate_limits,'paused',return_value=None), \
                 patch.object(quota.command_policy,'on_post_rate_limit',return_value=None) as adaptive:
                connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value=(method,'channel')
                quota.finish('request','failed',403,reason)
                self.assertEqual(adaptive.called,called)


if __name__=='__main__':unittest.main()
