from datetime import datetime, timezone
import json
import unittest
import uuid

from presenter_sessions import advance_sessions, advance_appearances


def observation(ts, value=None, status='no_clothing_change_detected', since=None, view='presenter_inset'):
    return {'id': str(uuid.uuid4()), 'stream_url': 'unit-test',
            'observed_at': datetime.fromtimestamp(ts, timezone.utc).isoformat(),
            'accepted_counter': value, 'clothing_status': status,
            'details': {'camera_view': {'view': view, 'tracking': {'changed_since': since}}}}


class PresenterSessionTests(unittest.TestCase):
    def test_brief_nonfolding_appearance_survives_unknown_camera_and_restart(self):
        obs = observation(30, status='change_pending', since=30)
        state, rows = advance_appearances({}, obs)
        appearance_id = rows[0]['id']
        self.assertEqual(rows[0]['folding_evidence'], 'no_accepted_increase_observed')
        state = json.loads(json.dumps(state))
        state, rows = advance_appearances(state, observation(
            60, status='camera_view_unknown_review_required', view=None))
        self.assertEqual(rows[0]['id'], appearance_id)
        self.assertEqual(rows[0]['status'], 'interrupted_review_required')
        self.assertIsNone(rows[0]['folding_started_at'])
        self.assertTrue(state['attribution_review_required'])
        self.assertNotIn('candidate', state)

    def test_brief_cue_clears_but_does_not_resume_old_person_fold_attribution(self):
        sessions, _, _ = self.step({}, 0, 100)
        appearances, _ = advance_appearances({}, observation(30, 100, 'change_pending', 30))
        obs = observation(60, 120)
        appearances, rows = advance_appearances(appearances, obs)
        self.assertEqual(rows[0]['status'], 'brief_appearance_review_required')
        obs['details']['presenter_attribution_review_required'] = appearances['attribution_review_required']
        sessions, rows, _ = advance_sessions(sessions, obs)
        self.assertEqual(rows[0]['last_counter'], 100)
        self.assertIsNone(rows[0]['folding_started_at'])

    def test_appearance_start_is_separate_from_folding_start(self):
        state, rows = advance_appearances({}, observation(30, 100, 'change_pending', 30))
        state, rows = advance_appearances(state, observation(60, 100, 'change_pending', 30))
        self.assertIsNone(rows[0]['folding_started_at'])
        state, rows = advance_appearances(state, observation(90, 120, 'possible_handover_review_required', 30))
        self.assertEqual(rows[0]['status'], 'sustained_appearance_review_required')
        self.assertEqual(rows[0]['folding_started_at'], observation(90)['observed_at'])
        self.assertNotIn('candidate', state)

    def test_manual_review_reset_resolves_attribution_hold(self):
        state, _ = advance_appearances({'attribution_review_required': True},
                                      observation(90, 120), manual_reset=True)
        self.assertNotIn('attribution_review_required', state)

    def step(self, state, ts, value=None, **kwargs):
        return advance_sessions(state, observation(ts, value, **kwargs))

    def test_initial_start_needs_an_increase_within_this_stint(self):
        state, rows, _ = self.step({}, 0, 100)
        session_id = rows[0]['id']
        self.assertIsNone(rows[0]['folding_started_at'])
        for ts, value in [(30,100),(60,None)]:
            state, rows, _ = self.step(state, ts, value)
            self.assertIsNone(rows[0]['folding_started_at'])
        state, rows, _ = self.step(state, 90, 120)
        self.assertEqual(rows[0]['folding_started_at'], observation(90)['observed_at'])
        self.assertEqual(rows[0]['folding_start_counter'],120)
        state, rows, _ = self.step(json.loads(json.dumps(state)), 120, 140)
        self.assertEqual(rows[0]['id'],session_id)
        self.assertEqual(rows[0]['folding_start_counter'],120)

    def test_pending_samples_become_new_stint_on_confirmation(self):
        state, _, _ = self.step({}, 0, 100)
        state, rows, _ = self.step(state,30,120,status='change_pending',since=30)
        self.assertEqual(rows, [])
        state, _, _ = self.step(state,60,140,status='change_pending',since=30)
        state, rows, promote = self.step(state,90,160,status='possible_handover_review_required',since=30)
        self.assertTrue(promote)
        old, new = rows
        self.assertEqual(old['ended_at'],observation(30)['observed_at'])
        self.assertEqual(old['last_counter'],100)
        self.assertEqual(new['detected_at'],observation(30)['observed_at'])
        self.assertEqual(new['confirmed_at'],observation(90)['observed_at'])
        self.assertEqual(new['folding_started_at'],observation(60)['observed_at'])
        self.assertEqual(new['baseline_counter'],120)
        self.assertNotEqual(old['id'],new['id'])
        self.assertNotIn('candidate',state)

    def test_cancelled_or_interrupted_candidate_is_not_a_person(self):
        for interruption in ['no_clothing_change_detected', 'baseline_created', 'camera_view_unknown_review_required']:
            state, rows, _ = self.step({},0,100)
            original = rows[0]['id']
            state, _, _ = self.step(state,30,120,status='change_pending',since=30)
            state, _, _ = self.step(state,60,140,status=interruption,
                                     view=None if interruption.startswith('camera') else 'presenter_main')
            self.assertNotIn('candidate',state)
            self.assertEqual(state['current']['id'],original)

    def test_counter_not_yet_readable_at_detection(self):
        state, _, _ = self.step({},0)
        state, rows, _ = self.step(state,30,100)
        self.assertIsNone(rows[0]['folding_started_at'])
        state, rows, _ = self.step(state,60,110)
        self.assertEqual(rows[0]['folding_start_counter'],110)

    def test_manual_reset_closes_current_stint(self):
        state, _, _ = self.step({},0,100)
        state, rows, _ = advance_sessions(state,observation(30,120,status='baseline_created'),manual_reset=True)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['end_reason'],'manual_baseline_reset')
        self.assertEqual(rows[1]['detection_reason'],'manual_baseline_reset')
        self.assertIsNone(rows[1]['folding_started_at'])

    def test_unknown_view_does_not_start_initial_stint(self):
        state, rows, _ = self.step({},0,100,status='camera_view_unknown_review_required',view=None)
        self.assertEqual(rows,[])
        self.assertNotIn('current',state)
