from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import youtube_chat as chat
import youtube_quota as quota


class PublicTextTests(unittest.TestCase):
    def test_internal_appearance_fields_are_never_accepted_for_public_chat(self):
        for text in ['Ubiór: sweter.', 'Włosy: brązowe, długie.', 'Zarost: broda.',
                     'Okulary: czarne.', 'Nakrycie głowy: czapka.', 'Dodatki: zegarek.',
                     'Opis osoby: test.', 'Opis wyglądu: test.', 'Hair: brown.',
                     'Ubi\u200bór: sweter.', '\u200bRekord: 3843. Ubiór: sweter.']:
            with self.subTest(text=text),self.assertRaises(ValueError):
                chat.validate_message(text)

    def test_transport_rejects_appearance_before_reservation_or_http(self):
        with patch.object(quota,'reserve') as reserve,patch.object(quota,'open_request') as http:
            with self.assertRaises(ValueError):
                quota.tracked_request('p','liveChatMessages.insert','fake',body={'snippet':{
                    'liveChatId':'chat','type':'textMessageEvent',
                    'textMessageDetails':{'messageText':'Ubiór: sweter. Włosy: brązowe.'}}},account_channel_id='channel')
            reserve.assert_not_called();http.assert_not_called()

    def test_numeric_records_and_help_remain_public(self):
        for text in ['Rekord zakończonej zmiany: 3843 złożeń.',
                     'Komendy: !ile, !pomoc, !rekord.',
                     'Złożenia aktualnej osoby: 1493. Tempo: 41,63 złożeń/min.']:
            self.assertEqual(chat.validate_message(text),text)


if __name__=='__main__':unittest.main()
