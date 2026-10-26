import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_chat as chat


class ChatTests(unittest.TestCase):
    def test_validation_and_video_target(self):
        self.assertEqual(chat.video_id('https://www.youtube.com/watch?v=vaiHWiExDXg'), 'vaiHWiExDXg')
        self.assertEqual(chat.video_id('https://youtu.be/vaiHWiExDXg'), 'vaiHWiExDXg')
        for url in ['https://evil.example/watch?v=vaiHWiExDXg', 'https://youtube.com/watch?v=x']:
            with self.assertRaises(ValueError):
                chat.video_id(url)
        self.assertEqual(chat.validate_message('x'*200), 'x'*200)
        for text in ['', '   ', 'x'*201]:
            with self.assertRaises(ValueError):
                chat.validate_message(text)

    def test_target_mismatch_never_posts(self):
        with patch.object(chat, 'initialize'), patch.object(chat, 'existing', return_value=None), patch.object(chat, 'context', return_value={'channel': {'id': 'other'}, 'chat_id': 'chat'}), patch.object(chat.quota, 'tracked_request') as api:
            with self.assertRaisesRegex(ValueError, 'changed'):
                chat.send('p', 'https://youtu.be/vaiHWiExDXg', 'test', 'key', 'expected', 'chat')
            api.assert_not_called()

    def test_duplicate_key_does_not_refresh_or_post(self):
        with patch.object(chat, 'initialize'), patch.object(chat, 'existing', return_value={'status': 'unknown'}), patch.object(chat, 'context') as context:
            self.assertEqual(chat.send('p', 'https://youtu.be/vaiHWiExDXg', 'test', 'key', 'channel', 'chat'), {'status': 'unknown'})
            context.assert_not_called()

    def test_context_accounts_for_identity_and_discovery(self):
        with patch.object(chat.auth, 'token', return_value='private'), patch.object(chat.auth, 'stored', return_value={}), patch.object(chat.auth, 'save'), patch.object(chat.quota, 'tracked_request', side_effect=[{'items': [{'id': 'channel', 'snippet': {'title': 'Title'}}]}, {'items': [{'liveStreamingDetails': {'activeLiveChatId': 'chat'}}]}]) as api:
            result = chat.context('p', 'https://youtu.be/vaiHWiExDXg')
            self.assertEqual(result['chat_id'], 'chat')
            self.assertEqual([call.args[1] for call in api.call_args_list], ['channels.list', 'videos.list'])


if __name__ == '__main__':
    unittest.main()
