import io
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_quota as q


class QuotaTests(unittest.TestCase):
    def test_pacific_day_and_dst_reset(self):
        self.assertEqual(q.api_day(datetime.fromisoformat('2026-10-26T06:30:00+00:00')).isoformat(), '2026-10-25')
        for stamp, expected in [('2026-03-08T00:00:00-08:00', 23), ('2026-11-01T00:00:00-07:00', 25)]:
            now = datetime.fromisoformat(stamp)
            hours = (q.reset_at(now).astimezone(timezone.utc)-now.astimezone(timezone.utc)).total_seconds()/3600
            self.assertEqual(hours, expected)
        with self.assertRaises(ValueError):
            q.api_day(datetime(2026, 1, 1))

    def test_reject_message_before_reservation(self):
        with patch.object(q, 'reserve') as reserve:
            with self.assertRaises(ValueError):
                q.tracked_request('p', 'liveChatMessages.insert', 'secret', body={'snippet': {
                    'type': 'textMessageEvent', 'liveChatId': 'c', 'textMessageDetails': {'messageText': 'x'*201}}})
            reserve.assert_not_called()

    def test_success_is_accounted(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"id":"message-id"}'
        response.__enter__.return_value.status = 200
        with patch.object(q, 'reserve', return_value='request-id'), patch.object(q, 'finish') as finish, patch.object(q, 'open_request', return_value=response):
            self.assertEqual(q.tracked_request('p', 'videos.list', 'secret')['id'], 'message-id')
            finish.assert_called_once_with('request-id', 'succeeded', 200, response_id='message-id')

    def test_http_failure_and_ambiguous_outcome_no_retry(self):
        errors = [HTTPError('url', 403, 'error', {}, io.BytesIO(b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}')), TimeoutError('secret')]
        for error, state in zip(errors, ['failed', 'unknown']):
            with self.subTest(state=state), patch.object(q, 'reserve', return_value='request-id'), patch.object(q, 'finish') as finish, patch.object(q, 'open_request', side_effect=error) as send:
                with self.assertRaises(q.YouTubeRequestFailed) as raised:
                    q.tracked_request('p', 'videos.list', 'secret')
                self.assertNotIn('secret', str(raised.exception))
                self.assertEqual(finish.call_args.args[1], state)
                send.assert_called_once()


if __name__ == '__main__':
    unittest.main()
