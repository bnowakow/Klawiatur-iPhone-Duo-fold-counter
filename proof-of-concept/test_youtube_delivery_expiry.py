"""A message expiring while quota is reserved must never reach HTTP."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import youtube_quota as quota


class DeliveryExpiryTests(unittest.TestCase):
    def test_expiry_during_reservation_prevents_http(self):
        start=datetime(2026,10,26,6,tzinfo=timezone.utc)
        with patch.object(quota,'datetime') as clock,patch.object(quota,'reserve',return_value='request') as reserve, \
                patch.object(quota,'finish') as finish,patch.object(quota,'urlopen') as http:
            clock.now.side_effect=[start,start+timedelta(seconds=2)]
            with self.assertRaises(quota.MessageExpired):
                quota.tracked_request('fixture','liveChatMessages.insert','fake-token',
                    body={'snippet':{'type':'textMessageEvent','liveChatId':'fixture-chat',
                                     'textMessageDetails':{'messageText':'fresh statistics'}}},
                    account_channel_id='fixture-channel',expires_at=start+timedelta(seconds=1))
            reserve.assert_called_once()
            finish.assert_called_once_with('request','failed',error_reason='local_message_expired')
            http.assert_not_called()


if __name__=='__main__':unittest.main()
