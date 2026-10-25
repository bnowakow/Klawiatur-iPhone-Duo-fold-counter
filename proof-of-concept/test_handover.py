import unittest

from PIL import Image

from detect_handover import advance, compare


class ClothingChangeTests(unittest.TestCase):
    def test_colour_change_and_unchanged(self):
        white = Image.new("RGB", (1920, 1080), "white")
        black = Image.new("RGB", (1920, 1080), "black")
        self.assertEqual(compare(white, white)[0], 0)
        self.assertEqual(compare(white, black)[0], 1)

    def test_requires_continuous_evidence(self):
        state = {}
        for t in range(0, 60, 10):
            state = advance(state, .8, t)
            self.assertEqual(state["status"], "change_pending")
        state = advance(state, .8, 60)
        self.assertEqual(state["status"], "possible_handover_review_required")
        self.assertEqual(advance(state, .1, 70)["status"], "no_clothing_change_detected")

    def test_night_or_capture_gap_resets_pending_evidence(self):
        state = advance({}, .8, 0)
        state = advance(state, .8, 3600)
        self.assertEqual(state["sustained_seconds"], 0)
        self.assertEqual(state["status"], "change_pending")

    def test_out_of_order_observations_rejected(self):
        with self.assertRaises(ValueError):
            advance(advance({}, .8, 20), .8, 10)
