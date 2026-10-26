from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import polling


class PollingTests(unittest.TestCase):
    def test_pending_appearance_gets_fast_followup_with_processing_headroom(self):
        state=polling.advance({'effective_interval_seconds':60},5,4,appearance_pending=True)
        self.assertEqual(state['effective_interval_seconds'],6.3)
        self.assertEqual(state['action'],'follow_appearance_candidate')
        state=polling.advance(state,12,4,appearance_pending=True)
        self.assertGreaterEqual(state['effective_interval_seconds'],15)
        self.assertEqual(state['action'],'increase')
        state=polling.advance({'effective_interval_seconds':60},1,4,
                              outcome='database_unavailable',appearance_pending=True)
        self.assertEqual(state['action'],'failure_backoff')

    def test_overrun_increases_above_elapsed_with_headroom(self):
        state=polling.advance({},5,3)
        self.assertEqual(state['effective_interval_seconds'],6.3)
        self.assertEqual(state['action'],'increase')
        self.assertGreater(polling.wait_seconds(state,5),0)
        state=polling.advance(state,8,3)
        self.assertGreaterEqual(state['effective_interval_seconds'],10)

    def test_one_fast_sample_does_not_collapse_backpressure(self):
        state=polling.advance({},5,3)
        for _ in range(11):
            state=polling.advance(state,1,3)
            self.assertEqual(state['effective_interval_seconds'],6.3)
        state=polling.advance(state,1,3)
        self.assertEqual(state['effective_interval_seconds'],5.7)
        self.assertEqual(state['action'],'recover')
        for _ in range(200):
            state=polling.advance(state,1,3)
        self.assertEqual(state['effective_interval_seconds'],3)

    def test_medium_utilization_resets_recovery_streak(self):
        state={'effective_interval_seconds':6,'stable_samples':11}
        state=polling.advance(state,5,3)
        self.assertEqual(state['stable_samples'],0)
        self.assertEqual(state['effective_interval_seconds'],6)

    def test_ceiling_leaves_cooldown_no_busy_retry(self):
        state=polling.advance({},100,3,maximum=60)
        self.assertEqual(state['action'],'saturated')
        self.assertEqual(polling.wait_seconds(state,100),60)

    def test_failures_back_off_even_when_fast(self):
        state=polling.advance({},.01,3,outcome='database_unavailable')
        self.assertEqual(state['effective_interval_seconds'],4.5)
        self.assertEqual(state['action'],'failure_backoff')

    def test_restart_during_database_failure_uses_local_state(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg={'data':Path(temp),'stream':'unit-polling'}
            state=polling.advance({},5,3)
            from types import SimpleNamespace
            from unittest.mock import Mock
            with patch.dict('sys.modules', {'psycopg':SimpleNamespace(connect=Mock(side_effect=RuntimeError('offline')))}):
                saved=polling.record(cfg,state)
                self.assertTrue(saved['queued'])
                self.assertEqual(polling.load(cfg),state)
            self.assertEqual(len(list((Path(temp)/'polling/pending').glob('*.json'))),1)

    def test_invalid_inputs_and_configured_ceiling(self):
        with self.assertRaises(ValueError):
            polling.advance({},float('nan'),3)
        with self.assertRaises(ValueError):
            polling.advance({},1,61,maximum=60)
        state=polling.advance({'effective_interval_seconds':20},1,4,maximum=10)
        self.assertEqual(state['effective_interval_seconds'],10)

    def test_manual_base_change_resets_only_unpressured_budget(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temp, patch.dict('sys.modules', {
                'psycopg':SimpleNamespace(connect=Mock(side_effect=RuntimeError('offline')))}):
            cfg={'data':Path(temp),'stream':'base-change','interval':4}
            polling.record(cfg,polling.advance({},1,10))
            self.assertEqual(polling.load(cfg),{})
            raised=polling.advance({},12,10)
            polling.record(cfg,raised)
            self.assertEqual(polling.load(cfg),raised)
