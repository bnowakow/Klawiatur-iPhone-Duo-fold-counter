"""Original morning pixels: new framing, pale/thin and fragmented LEDs."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from camera_views import PROFILE_PATH, select_view
from read_counter import recognize
import supervisor

ROOT = Path(__file__).parent / 'test-data/2026-10-26-morning-resumption'


class MorningResumptionTests(unittest.TestCase):
    def test_every_original_counter_including_fragmented_last_digits(self):
        for row in json.loads((ROOT / 'manifest.json').read_text()):
            with self.subTest(frame=row['file']), Image.open(ROOT / row['file']) as image:
                result, _ = recognize(image)
                self.assertEqual(result['value'], row['expected_counter'], result)

    def test_new_main_and_existing_inset_remain_separate_layouts(self):
        profiles = json.loads(PROFILE_PATH.read_text())
        for name, view in [('empty-main.jpg', 'presenter_main_morning'),
                           ('folding-main.jpg', 'presenter_main_morning'),
                           ('folding-inset.jpg', 'presenter_inset')]:
            with self.subTest(frame=name), Image.open(ROOT / name) as image:
                self.assertEqual(select_view(image, profiles)[0], view)

    def test_original_frames_resume_after_pause_and_keep_counting(self):
        base = datetime(2026, 10, 26, 5, tzinfo=timezone.utc)
        state = {'presenters': {'night_closed': {'counter': 63333, 'session_id': 'previous'}},
                 'appearances': {'attribution_review_required': True},
                 'counter': {'anchor': {'value': 63333, 'time': base.timestamp()-60},
                             'rates': [.7]}}
        with tempfile.TemporaryDirectory() as temp:
            cfg = dict(supervisor.config(), data=Path(temp), interval=48, effective_interval=48)
            for seconds, name in [(0, 'empty-main-later.jpg'), (48, 'folding-main.jpg'),
                                  (96, 'folding-inset.jpg'), (356, 'folding-inset-later.jpg')]:
                with patch('supervisor.datetime', wraps=datetime) as clock:
                    clock.now.return_value = base+timedelta(seconds=seconds)
                    obs = supervisor.observation(cfg)
                    state = supervisor.analyze(cfg, state, obs, ROOT / name)
                if seconds == 48:
                    self.assertEqual(obs['counter_status'], 'provisional')
                    self.assertNotIn('current', state['presenters'])
                if seconds == 96:
                    self.assertEqual(obs['counter_status'], 'accepted_rebased')
                    self.assertFalse(obs['details']['presenter_attribution_review_required'])
                    self.assertEqual(state['presenters']['current']['detection_reason'],
                                     'activity_resumed_after_night_pause')
            self.assertEqual(obs['accepted_counter'], 63599)
            self.assertEqual(state['presenters']['current']['folding_start_counter'], 63599)

    def test_resume_never_accepts_a_decrease_or_impossible_jump(self):
        cfg = supervisor.config()
        state = {'anchor': {'value': 63333, 'time': 0}, 'rates': [.7]}
        for value in (6333, 99999):
            result, updated = supervisor.validate_counter(value, 10, state, cfg,
                                                          resuming_after_pause=True)
            self.assertEqual(result['status'], 'rejected')
            self.assertEqual(updated, state)
