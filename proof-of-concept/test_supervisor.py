from datetime import datetime, timezone
import unittest

from supervisor import active, config, validate_counter
from detect_handover import advance


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.state = {"anchor": {"value": 1000000, "time": 100}, "rates": [.7]*5}

    def test_spike_and_decrease_do_not_poison_anchor(self):
        for value in (999999, 1700000):
            result, state = validate_counter(value, 130, self.state, self.cfg)
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(state, self.state)
            recovered, _ = validate_counter(1000042, 160, state, self.cfg)
            self.assertEqual(recovered["status"], "accepted")

    def test_steady_reading_above_million(self):
        result, _ = validate_counter(1000021, 130, self.state, self.cfg)
        self.assertEqual(result["accepted"], 1000021)
        self.assertAlmostEqual(result["rate"], .7)

    def test_pause_does_not_train_rate_to_zero(self):
        result, state = validate_counter(1000000, 130, self.state, self.cfg)
        self.assertEqual(result["status"], "accepted_paused")
        self.assertEqual(state["rates"], self.state["rates"])

    def test_slow_and_unknown_are_kept_unaccepted(self):
        result, state = validate_counter(1000001, 220, self.state, self.cfg)
        self.assertEqual(result["status"], "suspect_slow")
        self.assertEqual(state, self.state)
        result, state = validate_counter(None, 220, self.state, self.cfg)
        self.assertIsNone(result["accepted"])
        self.assertEqual(state, self.state)

    def test_start_and_overnight_gap_need_two_samples(self):
        for initial in ({}, self.state):
            result, state = validate_counter(1000500, 1000, initial, self.cfg)
            self.assertEqual(result["status"], "provisional")
            result, state = validate_counter(1000521, 1030, state, self.cfg)
            self.assertEqual(result["status"], "accepted_rebased")

    def test_long_gap_still_rejects_decrease_and_impossible_jump(self):
        for value in (999999, 1100000):
            result, state = validate_counter(value, 1000, self.state, self.cfg)
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(state, self.state)

    def test_clothing_change_can_persist_at_30_second_interval(self):
        state = {}
        for ts in (0, 30, 60):
            state = advance(state, .8, ts, max_gap=75)
        self.assertEqual(state["status"], "possible_handover_review_required")

    def test_faster_sampling_still_needs_sixty_seconds_to_confirm(self):
        state = {}
        for ts in range(0, 60, 10):
            state = advance(state, .8, ts, max_gap=25)
            self.assertEqual(state['status'], 'change_pending')
        state = advance(state, .8, 60, max_gap=25)
        self.assertEqual(state['status'], 'possible_handover_review_required')

    def test_unknown_camera_evidence_survives_frame_retention(self):
        import os
        from pathlib import Path
        import tempfile
        from PIL import Image
        from supervisor import analyze, observation, prune

        with tempfile.TemporaryDirectory() as temp:
            cfg = dict(self.cfg, data=Path(temp) / "data")
            fixture = Path(temp) / "unknown.png"
            Image.new("RGB", (1920, 1080), "black").save(fixture)
            obs = observation(cfg)
            analyze(cfg, {}, obs, fixture)
            self.assertEqual(obs["clothing_status"], "camera_view_unknown_review_required")
            archive = Path(obs["details"]["camera_review_path"])
            frame = Path(obs["frame_path"])
            original = frame.read_bytes()
            os.utime(frame.parent, (0, 0))
            import frame_retention
            frame_retention.register(cfg["data"], obs)
            prune(cfg)
            self.assertTrue(frame.exists())
            self.assertEqual((archive / "frame.jpg").read_bytes(), original)
            self.assertTrue((archive / "observation.json").exists())

    def test_morning_resumption_resolves_stale_review_and_new_clothing_candidate(self):
        from datetime import timedelta
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from PIL import Image, ImageDraw
        from supervisor import analyze, observation

        base = datetime(2026, 10, 26, 5, tzinfo=timezone.utc)
        state = {'presenters': {'night_closed': {'session_id': 'previous-stint', 'counter': 63333}},
                 'appearances': {'attribution_review_required': True},
                 'counter': {'anchor': {'value': 63333, 'time': base.timestamp()}, 'rates': [.7]}}
        with tempfile.TemporaryDirectory() as temporary:
            cfg = dict(self.cfg, data=Path(temporary))
            fixture = Path(temporary) / 'morning.png'
            def camera(*args, **kwargs):
                status = 'baseline_created' if kwargs.get('reset') else (
                    'change_pending' if index <= 2 else 'no_clothing_change_detected')
                return ({}, status, .8, None, {'view': 'presenter_main',
                        'tracking': {'changed_since': base.timestamp()+10}})
            for index, value in [(1, 63340), (2, 63347), (3, 63354)]:
                image = Image.new('RGB', (32, 32), 'white')
                ImageDraw.Draw(image).rectangle((0, 0, index, 5), fill='black')
                image.save(fixture)
                with patch('supervisor.datetime', wraps=datetime) as clock, \
                     patch('supervisor.recognize', return_value=({'value': value, 'digits': []}, None)), \
                     patch('supervisor.observe', side_effect=camera):
                    clock.now.return_value = base+timedelta(seconds=index*10)
                    obs = observation(cfg)
                    state = analyze(cfg, state, obs, fixture)
                if index == 1:
                    self.assertEqual(obs['counter_status'], 'provisional')
                    self.assertNotIn('current', state['presenters'])
                    continue
                self.assertFalse(obs['details']['presenter_attribution_review_required'])
                self.assertNotIn('candidate', state['appearances'])
                if index == 2:
                    rows = obs['details']['presenter_appearance_updates']
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]['status'], 'resolved_on_activity_resumption_review_required')
                    self.assertEqual(state['presenters']['current']['detection_reason'],
                                     'activity_resumed_after_night_pause')
            self.assertEqual(state['presenters']['current']['folding_start_counter'], 63354)

    def test_sustained_shirt_change_creates_only_one_new_stint(self):
        from datetime import timedelta
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from PIL import Image, ImageDraw
        from supervisor import analyze, observation

        base = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        state, ids, statuses = {}, set(), []
        with tempfile.TemporaryDirectory() as temp:
            cfg = dict(self.cfg, data=Path(temp) / "data")
            fixture = Path(temp) / "frame.png"
            for i in range(10):
                image = Image.open(Path(__file__).parent / "latest/live-frame.jpg").convert("RGB")
                draw = ImageDraw.Draw(image)
                draw.rectangle((0, 0, i+1, 4), fill="black")
                if i >= 2:
                    draw.rectangle((922, 551, 1248, 670), fill="black")
                    # A whole-shirt colour change also reaches the exposed
                    # shoulder; a chest-only patch models an arm/phone occlusion.
                    draw.rectangle((778, 508, 835, 551), fill="black")
                image.save(fixture)
                with patch("supervisor.datetime", wraps=datetime) as clock, \
                        patch("supervisor.recognize", return_value=({"value":1000+i*7,"digits":[]},None)):
                    clock.now.return_value = base + timedelta(seconds=i*10)
                    obs = observation(cfg)
                    state = analyze(cfg, state, obs, fixture)
                ids.add(obs["details"]["presenter_session_id"])
                statuses.append(obs["clothing_status"])
            self.assertEqual(len(ids), 2)
            self.assertEqual(statuses.count("possible_handover_review_required"), 1)
            self.assertEqual(statuses[-1], "no_clothing_change_detected")
            self.assertEqual(state["presenters"]["current"]["detected_at"],
                             (base+timedelta(seconds=20)).isoformat())
            self.assertEqual(state["presenters"]["current"]["folding_started_at"],
                             (base+timedelta(seconds=30)).isoformat())

    def test_initial_range_accepts_both_published_endpoints(self):
        cfg = dict(self.cfg, slack=0)
        for delta in (295, 567):
            initial = {"anchor": {"value": 1000000, "time": 0}, "rates": []}
            result, _ = validate_counter(1000000+delta, 600, initial, cfg)
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(result["rate_model"], "published_initial_range")
            self.assertEqual(result["expected_rate_range"], [cfg["expected_min"], cfg["expected_max"]])

    def test_initial_range_rejects_outside_values_without_training(self):
        cfg = dict(self.cfg, slack=0)
        initial = {"anchor": {"value": 1000000, "time": 0}, "rates": []}
        for delta, expected in [(294, "suspect_slow"), (568, "rejected")]:
            result, state = validate_counter(1000000+delta, 600, initial, cfg)
            self.assertEqual(result["status"], expected)
            self.assertEqual(state, initial)
        result, state = validate_counter(1000000, 600, initial, cfg)
        self.assertEqual(result["status"], "accepted_paused")
        self.assertEqual(state["rates"], [])

    def test_initial_range_checks_second_provisional_sample(self):
        initial = {"pending": {"value": 1000, "time": 0}}
        result, state = validate_counter(1100, 30, initial, self.cfg)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(state, initial)
        result, _ = validate_counter(1021, 30, initial, self.cfg)
        self.assertEqual(result["status"], "accepted_rebased")
        self.assertEqual(result["rate_model"], "published_initial_range")

    def test_learned_median_replaces_initial_bounds(self):
        result, _ = validate_counter(1000021, 130, self.state, self.cfg)
        self.assertEqual(result["rate_model"], "recent_median")
        self.assertAlmostEqual(result["expected_rate_range"][0], .35)
        self.assertAlmostEqual(result["expected_rate_range"][1], 1.05)

    def test_default_range_matches_published_completed_stints(self):
        import json
        import os
        from pathlib import Path
        from unittest.mock import patch
        source = json.loads((Path(__file__).parent / "test-data/2026-10-25-rate-range/published-source.json").read_text())
        table = source["parsed"]
        index = table["headers"].index("Złożenia na minutę")
        rates = [float(row[index].replace(",", "."))/60 for row in table["rows"]
                 if row[0].startswith("2026-") and row[1].isdigit() and row[2].isdigit()
                 and row[index] and float(row[index].replace(",", ".")) > 0]
        with patch.dict(os.environ, {}, clear=True):
            cfg = config()
        self.assertEqual(len(rates), 27)
        self.assertAlmostEqual(cfg["expected_min"], min(rates))
        self.assertAlmostEqual(cfg["expected_max"], max(rates))

    def test_rate_range_configuration_validation(self):
        from unittest.mock import patch
        import os
        for low, high in [("1", ".5"), (".5", "6"), ("0", "1"), ("nan", "1")]:
            with patch.dict(os.environ, {"EXPECTED_RATE_MIN": low, "EXPECTED_RATE_MAX": high, "MAX_RATE":"5"}):
                with self.assertRaises(ValueError):
                    config()

    def test_active_hours_in_warsaw(self):
        from datetime import time
        cfg = dict(self.cfg, start=time(8), end=time(23))
        self.assertFalse(active(datetime(2026, 10, 25, 2, tzinfo=timezone.utc), cfg))
        self.assertTrue(active(datetime(2026, 10, 25, 12, tzinfo=timezone.utc), cfg))
        cfg.update(start=time(22), end=time(6))
        self.assertTrue(active(datetime(2026, 10, 25, 23, tzinfo=timezone.utc), cfg))


if __name__ == "__main__":
    unittest.main()
