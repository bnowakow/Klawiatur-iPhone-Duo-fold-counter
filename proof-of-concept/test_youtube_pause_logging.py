from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import youtube_rate_limits as limits
import youtube_quota as quota


class PauseLoggingTests(unittest.TestCase):
    def test_stdout_includes_remaining_delay_and_local_deadline(self):
        now=datetime(2026,10,26,6,tzinfo=timezone.utc)
        output,errors=io.StringIO(),io.StringIO()
        with redirect_stdout(output),redirect_stderr(errors):
            limits.log_pause('youtube_posting_rate_limited','channel',
                             now+timedelta(seconds=600,microseconds=1),now=now,http_status=403)
        record=json.loads(output.getvalue())
        self.assertEqual(errors.getvalue(),'')
        self.assertEqual(record['level'],'ERROR')
        self.assertEqual(record['retry_in_seconds'],601)
        self.assertEqual(record['retry_in'],'00:10:01')
        self.assertEqual(record['not_before_warsaw'],'2026-10-26T07:10:00.000001+01:00')
        self.assertEqual(record['http_status'],403)

    def test_expired_delay_is_zero_and_output_is_flushed(self):
        now=datetime(2026,10,26,6,tzinfo=timezone.utc)
        with patch('builtins.print') as output:
            limits.log_pause('posting_cooldown_active','channel',now-timedelta(seconds=1),now=now)
        output.assert_called_once()
        self.assertTrue(output.call_args.kwargs['flush'])
        self.assertEqual(json.loads(output.call_args.args[0])['retry_in_seconds'],0)

    def test_provider_rejection_logs_after_database_commit(self):
        now=datetime.now(timezone.utc)
        with patch.object(quota,'connect') as connect,patch.object(limits,'record_result'), \
                patch.object(limits,'paused',return_value=now+timedelta(seconds=600)), \
                patch.object(limits,'log_pause') as log:
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value=('liveChatMessages.insert','channel')
            quota.finish('request','failed',403,'rateLimitExceeded')
        connect.return_value.__exit__.assert_called_once()
        log.assert_called_once_with('youtube_posting_rate_limited','channel',now+timedelta(seconds=600),
                                   reason='rateLimitExceeded',request_id='request',http_status=403)


if __name__=='__main__':unittest.main()
