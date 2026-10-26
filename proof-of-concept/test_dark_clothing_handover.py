from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from PIL import Image

from camera_views import observe
from repair_presenter_handover import split_sessions
from presenter_sessions import new_session, counter_sample, advance_sessions, advance_appearances

ROOT = Path(__file__).parent / 'test-data/2026-10-26-dark-clothing-handover'


class DarkClothingHandoverTests(unittest.TestCase):
    def observe(self, name, state, timestamp):
        with Image.open(ROOT / name) as image:
            return observe(image, state, timestamp, str(timestamp), (.48,.51,.65,.62), .45, 60, 60)

    def test_graphic_to_plain_black_top_requires_sustained_evidence(self):
        state, _, _, _, _ = self.observe('last-old-main.jpg', {}, 0)
        for ts, name in [(54, 'first-new-main.jpg'), (108, 'new-main-second.jpg'),
                         (163, 'new-main-later.jpg')]:
            state, status, _, _, details = self.observe(name, state, ts)
            self.assertLess(details['colour_score'], .45)
            self.assertGreater(max(details['garment_scores']), .45)
            self.assertEqual(status, 'change_pending' if ts < 163 else 'possible_handover_review_required')

    def test_previous_graphic_shirt_poses_do_not_trigger_a_handover(self):
        state, _, _, _, _ = self.observe('last-old-main.jpg', {}, 0)
        for ts, name in [(54, 'old-main.jpg'), (108, 'old-main-later.jpg')]:
            state, status, _, _, _ = self.observe(name, state, ts)
            self.assertEqual(status, 'no_clothing_change_detected')

    def test_upgrade_reconstructs_features_from_old_frame_not_new_shirt(self):
        profiles = json.loads((ROOT / 'previous-layouts.json').read_text())
        with Image.open(ROOT / 'last-old-main.jpg') as old:
            state, _, _, _, _ = observe(old, {}, 0, 'old', (.48,.51,.65,.62), .45, 60, 60,
                                        profiles=profiles)
        with tempfile.TemporaryDirectory() as temp, Image.open(ROOT / 'first-new-main.jpg') as new:
            frame = Path(temp) / 'old' / 'frame.jpg'
            frame.parent.mkdir()
            shutil.copyfile(ROOT / 'last-old-main.jpg', frame)
            migrated, status, _, _, details = observe(new, state, 54, 'new',
                (.48,.51,.65,.62), .45, 60, 60, evidence_root=temp)
        self.assertEqual(status, 'change_pending')
        self.assertTrue(details['garment_baseline_reconstructed'])
        self.assertEqual(details['baseline_observation'], 'old')
        self.assertNotEqual(migrated['config'], state['config'])

    def test_upgrade_without_original_evidence_holds_attribution(self):
        profiles = json.loads((ROOT / 'previous-layouts.json').read_text())
        with Image.open(ROOT / 'last-old-main.jpg') as old:
            state, _, _, _, _ = observe(old, {}, 0, 'old', (.48,.51,.65,.62), .45, 60, 60,
                                        profiles=profiles)
        with Image.open(ROOT / 'first-new-main.jpg') as new:
            state, status, _, _, camera = observe(new, state, 54, 'new',
                (.48,.51,.65,.62), .45, 60, 60)
        self.assertEqual(status, 'clothing_baseline_review_required')
        obs = {'id': 'new', 'stream_url': 'test', 'observed_at': '2026-10-26T06:00:26+00:00',
               'accepted_counter': 66003, 'clothing_status': status,
               'details': {'camera_view': camera}}
        sessions, rows, promote = advance_sessions({}, obs)
        self.assertEqual(rows, [])
        self.assertFalse(promote)
        self.assertNotIn('current', sessions)
        old_obs = dict(obs, id='old', accepted_counter=65980)
        current = new_session(old_obs, old_obs['observed_at'], 'test')
        counter_sample(current, old_obs)
        sessions, rows, _ = advance_sessions({'current': current}, obs)
        self.assertEqual(sessions['current']['last_counter'], 65980)

    def test_real_transition_automatically_creates_one_separate_folding_stint(self):
        clothing, sessions, appearances = {}, {}, {}
        base = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        for ts, file, count in [(0, 'last-old-main.jpg', 65980),
                                (54, 'first-new-main.jpg', 66003),
                                (108, 'new-main-second.jpg', None),
                                (163, 'new-main-later.jpg', 66122),
                                (190, 'new-main-later.jpg', 66140)]:
            clothing, status, _, _, camera = self.observe(file, clothing, ts)
            obs = {'id': str(ts), 'stream_url': 'test',
                   'observed_at': (base+timedelta(seconds=ts)).isoformat(),
                   'accepted_counter': count, 'clothing_status': status,
                   'details': {'camera_view': camera}}
            appearances, _ = advance_appearances(appearances, obs)
            obs['details']['presenter_attribution_review_required'] = bool(
                appearances.get('attribution_review_required'))
            sessions, updates, promoted = advance_sessions(sessions, obs)
            if ts == 0:
                old_id = sessions['current']['id']
            if ts in (54, 108):
                self.assertEqual(sessions['current']['last_counter'], 65980)
            if ts == 163:
                self.assertTrue(promoted)
                self.assertEqual(updates[0]['id'], old_id)
                self.assertEqual(updates[0]['last_counter'], 65980)
                new_id = sessions['current']['id']
                self.assertNotEqual(old_id, new_id)
                self.assertEqual(sessions['current']['baseline_counter'], 66003)
                clothing, _, _, _, _ = self.observe(file, {}, ts)
            if ts == 190:
                self.assertFalse(promoted)
                self.assertEqual(sessions['current']['id'], new_id)
                self.assertEqual(sessions['current']['last_counter'], 66140)

    def test_new_plain_shirt_poses_do_not_trigger_a_handover(self):
        state, _, _, _, _ = self.observe('first-new-main.jpg', {}, 0)
        for ts, name in [(54, 'new-main-second.jpg'), (108, 'new-main-later.jpg')]:
            state, status, _, _, _ = self.observe(name, state, ts)
            self.assertEqual(status, 'no_clothing_change_detected')

    def test_reviewed_split_keeps_boundary_folds_unattributed(self):
        base = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        def obs(seconds, value):
            return {'id': str(seconds), 'stream_url': 'test',
                    'observed_at': (base+timedelta(seconds=seconds)).isoformat(),
                    'accepted_counter': value}
        initial, before = obs(0,1000), obs(60,1100)
        current = new_session(initial, initial['observed_at'], 'first_observed')
        current['confirmed_at'] = initial['observed_at']
        after = [obs(90,1104), obs(120,1120), obs(150,1140)]
        for row in [initial, before]+after:
            counter_sample(current, row)
        backup = json.loads(json.dumps(current))
        old, new, evidence = split_sessions(current, before, after,
                                            obs(180,1140)['observed_at'], 'audit')
        self.assertEqual(current, backup)
        self.assertEqual(old['last_counter'],1100)
        self.assertEqual(old['ended_at'],before['observed_at'])
        self.assertEqual(new['baseline_counter'],1104)
        self.assertEqual(new['folding_start_counter'],1120)
        self.assertEqual(new['last_counter'],1140)
        self.assertEqual(evidence['unattributed_boundary_folds'],4)
        self.assertFalse(evidence['exact_stop_verified'])
        with self.assertRaises(ValueError):
            split_sessions(current, before, after[:1], obs(180,1140)['observed_at'], 'audit')
