import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_auth as auth


class AuthTests(unittest.TestCase):
    def test_callback_rejects_wrong_state_host_duplicate_code_and_denial(self):
        self.assertEqual(auth.callback_code('/oauth/callback?state=expected&code=private', 'localhost:8765', 'expected'), 'private')
        for path, host in [('/oauth/callback?state=wrong&code=c', 'localhost:8765'),
                           ('/oauth/callback?state=expected&code=c', 'evil.example'),
                           ('/other?state=expected&code=c', 'localhost:8765'),
                           ('/oauth/callback?state=expected&code=a&code=b', 'localhost:8765')]:
            with self.assertRaises(ValueError):
                auth.callback_code(path, host, 'expected')
        with self.assertRaises(auth.AuthorizationRequired):
            auth.callback_code('/oauth/callback?state=expected&error=access_denied', 'localhost:8765', 'expected')

    def test_atomic_private_credentials_and_disconnect_even_when_offline(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'YOUTUBE_AUTH_DIR': folder}):
            with auth.locked():
                auth.save({'credentials': {'token': 'secret'}})
                self.assertEqual(auth.stored()['credentials']['token'], 'secret')
                self.assertEqual((Path(folder)/'credentials.json').stat().st_mode & 0o777, 0o600)
                with patch.object(auth, 'urlopen', side_effect=TimeoutError('secret')):
                    self.assertEqual(auth.disconnect(), {'connected': False, 'remote_revocation_confirmed': False})
                with self.assertRaises(auth.AuthorizationRequired):
                    auth.stored()

    def test_refresh_saved_and_project_mismatch_blocked(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'YOUTUBE_AUTH_DIR': folder}):
            auth.save({'project_id': 'p', 'credentials': {'token': 'old'}})
            mock = MagicMock(valid=False, token='new')
            mock.to_json.return_value = json.dumps({'token': 'new', 'refresh_token': 'refresh'})
            with patch('google.oauth2.credentials.Credentials.from_authorized_user_info', return_value=mock):
                self.assertEqual(auth.token('p'), 'new')
                mock.refresh.assert_called_once()
                self.assertEqual(auth.stored()['credentials']['refresh_token'], 'refresh')
            with self.assertRaises(auth.AuthorizationRequired):
                auth.token('other')

    def test_revoked_token_does_not_leak_provider_details(self):
        from google.auth.exceptions import RefreshError
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'YOUTUBE_AUTH_DIR': folder}):
            auth.save({'project_id': 'p', 'credentials': {}})
            mock = MagicMock(valid=False)
            mock.refresh.side_effect = RefreshError('private-provider-data')
            with patch('google.oauth2.credentials.Credentials.from_authorized_user_info', return_value=mock):
                with self.assertRaises(auth.AuthorizationRequired) as raised:
                    auth.token('p')
                self.assertNotIn('private-provider-data', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
