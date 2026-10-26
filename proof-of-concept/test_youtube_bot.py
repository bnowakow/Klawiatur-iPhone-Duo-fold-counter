from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_bot as bot
from utilities.current_person_message import message_for_pace


class BotTests(unittest.TestCase):
    def test_compact_reply_preserves_values_and_exact_disclaimer_within_limit(self):
        config = ('current_person',True,600,bot.DISCLAIMER,120)
        text = message_for_pace('44.68', 1234567, compact=True)
        with patch('utilities.current_person_message.generate_current_message', return_value=text) as generate:
            reply = bot.build_reply('stream',config)
        generate.assert_called_once_with('stream',compact=True)
        self.assertIn('1234567',reply)
        self.assertIn('44,68',reply)
        self.assertIn('2681',reply)
        self.assertTrue(reply.endswith(bot.DISCLAIMER))
        self.assertLessEqual(len(reply),200)

    def test_only_exact_text_commands_match_no_bot_reply_loop(self):
        for text in ['!ile', ' !ILE ']:
            self.assertEqual(bot.parsed_command({'type':1,'text':text}), '!ile')
        for item in [{'type':1,'text':'!ile extra'}, {'type':1,'text':'prefix !ile'},
                     {'type':1,'text':bot.DISCLAIMER},{'type':2,'text':'!ile'}]:
            self.assertIsNone(bot.parsed_command(item))


if __name__=='__main__':
    unittest.main()
