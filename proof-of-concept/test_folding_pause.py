from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock

from presenter_sessions import advance_sessions
from test_presenter_sessions import observation
from utilities.current_person_message import current_measurements, PaceUnavailable


class FoldingPauseTests(unittest.TestCase):
    def paused(self):
        return {'activity_pause': {'kind': 'freeze_test', 'counter': 75000,
                                    'session_id': 'old'}}

    def test_missing_counter_stationary_counter_and_unknown_view_do_not_resume(self):
        for value, view in [(None, None), (None, 'presenter_main'), (75000, 'presenter_main'),
                             (74999, 'presenter_main'), (75010, None)]:
            with self.subTest(value=value, view=view):
                state = json.loads(json.dumps(self.paused()))
                for ts in (10, 20, 30):
                    state, rows, promote = advance_sessions(state, observation(ts, value, view=view))
                    self.assertEqual(rows, [])
                    self.assertFalse(promote)
                    self.assertNotIn('current', state)
                    self.assertIn('activity_pause', state)

    def test_two_increasing_readings_in_known_views_create_a_separate_stint(self):
        state, rows, _ = advance_sessions(self.paused(), observation(10, 75010))
        self.assertEqual(rows, [])
        state = json.loads(json.dumps(state))
        state, rows, promoted = advance_sessions(state, observation(20, 75015))
        self.assertTrue(promoted)
        self.assertNotIn('activity_pause', state)
        self.assertNotEqual(rows[0]['id'], 'old')
        self.assertEqual(rows[0]['baseline_counter'], 75010)
        self.assertEqual(rows[0]['last_counter'], 75015)
        self.assertEqual(rows[0]['detection_reason'], 'activity_resumed_after_confirmed_pause')

    def test_gap_or_unreadable_frame_interrupts_resumption(self):
        for second in (observation(15, None), observation(60, 75015)):
            state, _, _ = advance_sessions(self.paused(), observation(10, 75010))
            state, rows, promoted = advance_sessions(state, second)
            self.assertFalse(promoted)
            self.assertEqual(rows, [])
            self.assertIn('activity_pause', state)

    def test_current_message_explains_the_freeze_pause(self):
        conn = Mock()
        conn.execute.return_value.fetchone.side_effect = [None, ({'kind': 'freeze_test'},)]
        with self.assertRaisesRegex(PaceUnavailable, 'test mrożenia'):
            current_measurements(conn, 'fixture', datetime.now(timezone.utc))
