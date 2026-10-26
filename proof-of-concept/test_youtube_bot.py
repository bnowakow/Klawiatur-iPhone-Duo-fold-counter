from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_bot as bot
from utilities.current_person_message import message_for_pace


class BotTests(unittest.TestCase):
    def test_link_is_exact_static_text_without_live_data_or_api_calls(self):
        with patch.object(bot.quota,'connect',side_effect=AssertionError('Unexpected database read')), \
                patch.object(bot.quota,'tracked_request',side_effect=AssertionError('Unexpected API request')):
            text=bot.HANDLERS['static_link']('stream',('static_link',True,600,bot.LINK_DISCLAIMER,120))
        self.assertEqual(text,'ipduotest kropka pl')
        self.assertEqual(bot.parsed_command({'type':1,'text':' !LINK '}),'!link')
        self.assertIsNone(bot.parsed_command({'type':1,'text':'!link extra'}))

    def test_help_includes_link_with_all_existing_commands(self):
        commands=[('!ile','liczba złożeń i tempo'),('!link','adres strony testu'),
                  ('!pomoc','lista komend'),('!samsung','wynik testu Samsunga')]
        with patch.object(bot,'available_commands',return_value=commands):
            text=bot.build_help_reply('stream',('help',True,600,bot.HELP_DISCLAIMER,120))
        for command,_ in commands:
            self.assertIn(command,text)
        self.assertTrue(text.endswith(bot.HELP_DISCLAIMER))
        self.assertLessEqual(len(text),200)

    def test_samsung_reads_cached_summary_without_live_measurements_or_http(self):
        connection=MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value=(bot.SAMSUNG_SUMMARY,)
        with patch.object(bot.quota,'connect',return_value=connection), patch.object(bot.quota,'tracked_request',side_effect=AssertionError('Unexpected API request')):
            text=bot.build_samsung_reply('stream',('cached_samsung',True,600,bot.SAMSUNG_DISCLAIMER,120))
        self.assertIn('Samsung Galaxy Z Flip3',text)
        self.assertIn('418 500',text)
        self.assertIn('nie domykał się',text)
        self.assertTrue(text.endswith(bot.SAMSUNG_DISCLAIMER))
        self.assertLessEqual(len(text),200)

    def test_help_lists_available_commands_and_keeps_its_disclaimer(self):
        config=('help',True,600,bot.HELP_DISCLAIMER,120)
        with patch.object(bot,'available_commands',return_value=[('!ile','liczba złożeń i tempo'),('!pomoc','lista komend')]):
            text=bot.build_help_reply('stream',config)
        self.assertIn('!ile — liczba złożeń i tempo',text)
        self.assertIn('!pomoc — lista komend',text)
        self.assertTrue(text.endswith(bot.HELP_DISCLAIMER))
        self.assertLessEqual(len(text),200)

    def test_long_descriptions_fall_back_to_complete_command_names(self):
        with patch.object(bot,'available_commands',return_value=[('!ile','x'*200),('!pomoc','y'*200)]):
            text=bot.build_help_reply('stream',('help',True,600,bot.HELP_DISCLAIMER,120))
        self.assertIn('Komendy: !ile, !pomoc.',text)
        self.assertLessEqual(len(text),200)

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
