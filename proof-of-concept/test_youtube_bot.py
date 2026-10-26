from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_bot as bot
from utilities.current_person_message import message_for_pace


class BotTests(unittest.TestCase):
    def test_polish_duration_including_plural_forms_and_short_streams(self):
        self.assertEqual(bot.polish_duration(59),'poniżej minuty')
        self.assertEqual(bot.polish_duration(86400+3600+60),'1 dzień, 1 godzina, 1 minuta')
        self.assertEqual(bot.polish_duration(2*86400+22*3600+14*60),'2 dni, 22 godziny, 14 minut')
        self.assertEqual(bot.polish_duration(12*86400+12*3600+22*60),'12 dni, 12 godzin, 22 minuty')
        with self.assertRaises(ValueError):
            bot.polish_duration(-1)

    def test_duration_uses_actual_start_across_dst_without_api_requests(self):
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        connection=MagicMock()
        started=datetime(2026,10,24,13,51,18,tzinfo=ZoneInfo('Europe/Warsaw'))
        now=datetime(2026,10,26,9,51,18,tzinfo=ZoneInfo('Europe/Warsaw'))
        connection.__enter__.return_value.execute.return_value.fetchone.return_value=(started,None)
        with patch.object(bot.quota,'connect',return_value=connection), \
                patch.object(bot.quota,'tracked_request',side_effect=AssertionError('Unexpected API call')), \
                patch.object(bot,'datetime',wraps=datetime) as clock:
            clock.now.return_value=now
            text=bot.build_duration_reply('stream',('stream_duration',True,600,bot.DURATION_DISCLAIMER,120))
        self.assertIn('1 dzień, 21 godzin',text)
        self.assertTrue(text.endswith(bot.DURATION_DISCLAIMER))
        self.assertLessEqual(len(text),200)
        connection.__enter__.return_value.execute.return_value.fetchone.return_value=(started,started.replace(day=25))
        with patch.object(bot.quota,'connect',return_value=connection):
            text=bot.build_duration_reply('stream',('stream_duration',True,600,bot.DURATION_DISCLAIMER,120))
        self.assertIn('zakończonej transmisji: 1 dzień, 1 godzina',text)

    def test_duration_does_not_invent_missing_api_start_time(self):
        connection=MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value=(None,None)
        with patch.object(bot.quota,'connect',return_value=connection):
            text=bot.build_duration_reply('stream',('stream_duration',True,600,bot.DURATION_DISCLAIMER,120))
        self.assertIn('Brak potwierdzonego czasu rozpoczęcia',text)
        self.assertEqual(bot.parsed_command({'type':1,'text':' !JAK-DŁUGO '}),'!jak-długo')

    def test_source_command_returns_repository_without_external_reads(self):
        with patch.object(bot.quota,'connect',side_effect=AssertionError('Unexpected database read')), \
                patch.object(bot.quota,'tracked_request',side_effect=AssertionError('Unexpected API request')):
            text=bot.HANDLERS['source_code']('stream',('source_code',True,600,bot.SOURCE_DISCLAIMER,120))
        self.assertEqual(text, 'Klawiatur-iPhone-Duo-fold-counter na githubie '+bot.SOURCE_DISCLAIMER)
        self.assertNotIn('https://',text)
        self.assertNotIn('http://',text)
        self.assertTrue(text.endswith(bot.SOURCE_DISCLAIMER))
        self.assertLessEqual(len(text),200)
        self.assertEqual(bot.parsed_command({'type':1,'text':' !KOD-ŹRÓDŁOWY '}),'!kod-źródłowy')
        self.assertIsNone(bot.parsed_command({'type':1,'text':'!kod-źródłowy extra'}))

    def test_record_is_numeric_only_and_never_reads_appearance_descriptions(self):
        connection=MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value=(3843,
            'Ubiór: sweter. Włosy: brązowe, długie. Okulary: brak widocznych okularów. Dodatki: zegarek.')
        with patch.object(bot.quota,'connect',return_value=connection):
            text=bot.build_record_reply('stream',('completed_record',True,600,bot.RECORD_DISCLAIMER,120))
        self.assertIn('3843 złożeń',text)
        self.assertNotIn('sweter',text)
        self.assertNotIn('Włosy',text)
        query=connection.__enter__.return_value.execute.call_args.args[0]
        self.assertNotIn('presenter_clothing_descriptions',query)
        self.assertNotIn('description',query)
        self.assertNotIn('Okulary',text)
        self.assertTrue(text.endswith(bot.RECORD_DISCLAIMER))
        self.assertLessEqual(len(text),200)

    def test_record_handles_no_completed_stints_missing_or_long_descriptions(self):
        for row in [None,(4000,None),(4000,'Ubiór: '+('bardzo '*100))]:
            connection=MagicMock()
            connection.__enter__.return_value.execute.return_value.fetchone.return_value=row
            with patch.object(bot.quota,'connect',return_value=connection):
                text=bot.build_record_reply('stream',('completed_record',True,600,bot.RECORD_DISCLAIMER,120))
            self.assertLessEqual(len(text),200)
            self.assertTrue(text.endswith(bot.RECORD_DISCLAIMER))
            if row:
                self.assertIn('4000 złożeń',text)
            else:
                self.assertIn('Brak zapisanej zakończonej zmiany',text)

    def test_link_is_exact_static_text_without_live_data_or_api_calls(self):
        with patch.object(bot.quota,'connect',side_effect=AssertionError('Unexpected database read')), \
                patch.object(bot.quota,'tracked_request',side_effect=AssertionError('Unexpected API request')):
            text=bot.HANDLERS['static_link']('stream',('static_link',True,600,bot.LINK_DISCLAIMER,120))
        self.assertEqual(text,'ipduotest kropka pl')
        self.assertEqual(bot.parsed_command({'type':1,'text':' !LINK '}),'!link')
        self.assertIsNone(bot.parsed_command({'type':1,'text':'!link extra'}))

    def test_help_includes_link_with_all_existing_commands(self):
        commands=[('!ile','liczba złożeń i tempo'),('!jak-długo','czas trwania transmisji'),
                  ('!kod-źródłowy','repozytorium na GitHubie'),('!link','adres strony testu'),
                  ('!pomoc','lista komend'),('!rekord','najlepsza zakończona zmiana'),
                  ('!samsung','wynik testu Samsunga')]
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
