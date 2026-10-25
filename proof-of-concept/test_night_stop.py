from datetime import datetime, time, timedelta, timezone
import json
import unittest
from zoneinfo import ZoneInfo

from presenter_sessions import advance_sessions
from test_presenter_sessions import observation


class NightStopTests(unittest.TestCase):
    def setUp(self):
        self.base = datetime(2026, 10, 25, 21, 58, tzinfo=ZoneInfo('Europe/Warsaw')).timestamp()
        self.cfg = {'timezone': ZoneInfo('Europe/Warsaw'), 'start': time(21,30),
                    'end': time(6,30), 'seconds': 180, 'max_gap': 75}

    def step(self, state, seconds, value=120, view='presenter_main'):
        obs = observation(self.base+seconds, value, view=view)
        state, rows, _ = advance_sessions(state, obs, night_config=self.cfg)
        return state, rows, obs

    def started(self):
        state, _, _ = self.step({}, 0, 100)
        state, _, _ = self.step(state, 30, 120)
        return state

    def test_stop_is_backdated_and_confirmation_is_separate(self):
        state = self.started()
        original = state['current']['id']
        for seconds in (60,90,120,150,180):
            state, rows, _ = self.step(state, seconds)
            self.assertIsNone(state['current']['ended_at'])
        state, rows, obs = self.step(json.loads(json.dumps(state)), 210)
        self.assertNotIn('current',state)
        self.assertEqual(rows[0]['id'], original)
        self.assertEqual(rows[0]['ended_at'], observation(self.base+30)['observed_at'])
        self.assertEqual(rows[0]['last_counter_at'], rows[0]['ended_at'])
        evidence = rows[0]['end_evidence']
        self.assertEqual(evidence['confirmed_at'], obs['observed_at'])
        self.assertFalse(evidence['presenter_absence_verified'])

    def test_stopped_counter_and_unknown_camera_can_close_with_review(self):
        state=self.started()
        for seconds in (60,90,120,150,180,210):
            state, rows, _ = self.step(state, seconds, view=None)
        self.assertNotIn('current',state)
        self.assertIsNone(rows[0]['end_evidence']['camera_view'])

    def test_22_oclock_alone_does_not_close_an_active_session(self):
        state=self.started()
        for seconds in range(60,330,30):
            state, _, _ = self.step(state, seconds, 120+seconds)
        self.assertIsNone(state['current']['ended_at'])

    def test_daytime_pause_does_not_close(self):
        self.base=datetime(2026,10,25,12,tzinfo=timezone.utc).timestamp()
        state=self.started()
        for seconds in range(60,330,30):
            state, _, _=self.step(state,seconds)
        self.assertIsNone(state['current']['ended_at'])

    def test_gaps_and_unreadable_frames_interrupt_confirmation(self):
        for value in (120, None):
            state=self.started()
            state, _, _=self.step(state,60)
            state, _, _=self.step(state,300,value)
            state, _, _=self.step(state,330)
            self.assertIn('current',state)
            self.assertIsNone(state['current']['ended_at'])

    def test_no_new_session_until_counter_resumes(self):
        state=self.started()
        original=state['current']['id']
        for seconds in (60,90,120,150,180,210):
            state, _, _=self.step(state,seconds)
        state, rows, _=self.step(state,240)
        self.assertEqual(rows,[])
        self.assertNotIn('current',state)
        state, rows, _=self.step(state,270,140)
        self.assertNotEqual(rows[0]['id'],original)
        self.assertEqual(rows[0]['detection_reason'],'activity_resumed_after_night_pause')
        self.assertIsNone(rows[0]['folding_started_at'])

    def test_empty_chair_clothing_cue_does_not_promote_a_nonfolding_stint(self):
        state=self.started()
        original=state['current']['id']
        for seconds in (60,90,120,150,180,210):
            obs=observation(self.base+seconds,120,
                            'change_pending' if seconds<120 else 'possible_handover_review_required',
                            since=self.base+60)
            state, rows, promote=advance_sessions(state,obs,night_config=self.cfg)
            self.assertFalse(promote)
            if seconds<210:self.assertEqual(state['current']['id'],original)
        self.assertNotIn('current',state)
        self.assertEqual(rows[0]['id'],original)
        self.assertIn('night_pause',rows[0]['end_reason'])


if __name__=='__main__':
    unittest.main()
