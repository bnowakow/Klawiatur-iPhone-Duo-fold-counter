from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import Mock

from utilities.current_person_message import current_pace, message_for_pace, PaceUnavailable


class CurrentPersonMessageTests(unittest.TestCase):
    def test_polish_message_and_hourly_estimate_from_unrounded_pace(self):
        self.assertEqual(message_for_pace(Decimal('45.824')),
                         'dotychczasowe tempo składania: 45,82 złożeń/min, '
                         'w tym tempie telefon powinien być złożony 2749 razy w ciągu godziny')
        self.assertIn('2749 razy', message_for_pace(Decimal('45.82499')))
        self.assertIn('45 złożeń/min', message_for_pace(45))
        self.assertIn('2700 razy', message_for_pace(45))

    def connection(self, age=10, review=False, candidate=None, clothing='no_clothing_change_detected'):
        now = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        conn = Mock()
        conn.execute.return_value.fetchone.side_effect = [
            (now-timedelta(minutes=10), now-timedelta(seconds=age), Decimal('45.82')),
            (now, 'analyzed', clothing, {'polling_interval_seconds': 4,
             'presenter_attribution_review_required': review, 'presenter_candidate_id': candidate})]
        return conn, now

    def test_current_person_average_is_used(self):
        conn, now = self.connection()
        self.assertEqual(current_pace(conn, 'test-stream', now), Decimal('45.82'))

    def test_stale_or_uncertain_person_never_generates_a_pace(self):
        for settings in ({'age': 61}, {'review': True}, {'candidate': 'pending'},
                         {'clothing': 'night_break_detected'}):
            with self.subTest(settings=settings):
                conn, now = self.connection(**settings)
                with self.assertRaises(PaceUnavailable):
                    current_pace(conn, 'test-stream', now)

    def test_missing_stint_and_invalid_paces_are_unavailable(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = None
        with self.assertRaises(PaceUnavailable):
            current_pace(conn, 'test-stream')
        for pace in ('NaN', 'Infinity', '-1'):
            with self.assertRaises(PaceUnavailable):
                message_for_pace(pace)
