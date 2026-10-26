import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from camera_views import observe, select_view, PROFILE_PATH

ROOT = Path(__file__).parent


@unittest.skipUnless((ROOT / "camera-check/live-frame.jpg").exists() and
                     (ROOT / "latest/live-frame.jpg").exists() and
                     (ROOT / "live-frame.jpg").exists(), "Real camera fixtures unavailable")
class CameraViewTests(unittest.TestCase):
    def setUp(self):
        self.profiles = json.loads(PROFILE_PATH.read_text())
        self.main = Image.open(ROOT / 'latest/live-frame.jpg').convert('RGB')
        self.inset = Image.open(ROOT / 'camera-check/live-frame.jpg').convert('RGB')

    def observe(self, image, state, ts):
        return observe(image, state, ts, str(ts), (.48,.51,.65,.62), .45, 60, 30)

    def test_real_views_and_original_frame(self):
        for image, expected in [(self.main, 'presenter_main'),
                                (self.inset, 'presenter_inset'),
                                (Image.open(ROOT / 'live-frame.jpg'), 'presenter_main')]:
            self.assertEqual(select_view(image, self.profiles)[0], expected)

    def test_faster_polling_keeps_existing_clothing_baseline(self):
        state, _, _, _, _ = self.observe(self.inset, {}, 0)
        state, status, score, _, details = observe(
            self.inset, state, 10, 'new-observation', (.48,.51,.65,.62), .45, 60, 10)
        self.assertEqual(status, 'no_clothing_change_detected')
        self.assertEqual(details['baseline_observation'], '0')
        self.assertEqual(score, 0)

    def test_threshold_change_preserves_baseline_but_restarts_pending_evidence(self):
        state, _, _, _, _ = self.observe(self.inset, {}, 0)
        state, status, _, _, details = observe(
            self.inset, state, 30, 'new', (.48,.51,.65,.62), .4, 90, 10)
        self.assertEqual(status, 'no_clothing_change_detected')
        self.assertEqual(details['baseline_observation'], '0')

    def test_changed_crop_requires_review_instead_of_learning_current_shirt(self):
        state, _, _, _, _ = self.observe(self.inset, {}, 0)
        for ts in (30, 60):
            state, status, _, _, details = observe(
                self.inset, state, ts, 'new', (.4,.5,.6,.6), .45, 60, 30)
            self.assertEqual(status, 'clothing_baseline_review_required')
            self.assertTrue(details['baseline_review_required'])
            self.assertEqual(state['views']['presenter_inset']['baseline_observation'], '0')

    def test_switch_selects_inset_shirt_and_keeps_separate_baselines(self):
        state, _, _, _, _ = self.observe(self.main, {}, 0)
        state, status, _, crop, details = self.observe(self.inset, state, 30)
        self.assertEqual(status, 'baseline_created')
        self.assertEqual(len(state['views']), 2)
        self.assertEqual(details['clothing_roi'], [.818,.833,.862,.876])
        self.assertGreater(sum(crop.resize((1,1)).getpixel((0,0)))/3, 180)
        state, status, score, _, _ = self.observe(self.main, state, 60)
        self.assertEqual(status, 'no_clothing_change_detected')
        self.assertEqual(score, 0)

    def test_changed_shirt_requires_persistence_in_same_view(self):
        state, _, _, _, _ = self.observe(self.inset, {}, 0)
        changed = self.inset.copy()
        ImageDraw.Draw(changed).rectangle((1570,899,1656,947), fill='black')
        for ts in (30,60,90):
            state, status, _, _, _ = self.observe(changed, state, ts)
        self.assertEqual(status, 'possible_handover_review_required')
        state, _, _, _, _ = self.observe(self.main, state, 120)
        state, status, _, _, _ = self.observe(changed, state, 150)
        self.assertEqual(status, 'change_pending')
        self.assertEqual(state['views']['presenter_inset']['tracking']['sustained_seconds'], 0)

    def test_unknown_scene_never_becomes_clothing_baseline(self):
        state, _, _, _, _ = self.observe(self.inset, {}, 0)
        state, status, score, crop, _ = self.observe(Image.new('RGB', (1920,1080), 'black'), state, 30)
        self.assertEqual(status, 'camera_view_unknown_review_required')
        self.assertIsNone(score)
        self.assertIsNone(crop)
        self.assertEqual(len(state['views']), 1)
        self.assertIsNone(state['last_view'])

    def test_real_brief_appearance_and_gray_sweatshirt_are_reviewed(self):
        root = ROOT / 'test-data/2026-10-25-brief-appearances'
        white = Image.open(root / 'white-before.jpg')
        state, _, _, _, _ = self.observe(white, {}, 0)
        state, status, _, _, _ = self.observe(Image.open(root / 'olive-candidate.jpg'), state, 30)
        self.assertEqual(status, 'change_pending')
        state, status, _, _, _ = self.observe(Image.open(root / 'unknown-transition.jpg'), state, 60)
        self.assertEqual(status, 'camera_view_unknown_review_required')
        for ts, filename in [(90, 'gray-after.jpg'), (120, 'gray-after.jpg'), (150, 'gray-after.jpg')]:
            state, status, _, _, details = self.observe(Image.open(root / filename), state, ts)
            self.assertGreater(details['luminance_score'], .60)
        self.assertEqual(status, 'possible_handover_review_required')
        state, status, _, _, details = self.observe(Image.open(root / 'gray-later.jpg'), state, 180)
        self.assertEqual(status, 'baseline_created')
        self.assertEqual(details['view'], 'presenter_main')
