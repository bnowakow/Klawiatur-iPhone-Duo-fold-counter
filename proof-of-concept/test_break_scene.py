from datetime import datetime, time, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

from camera_views import detect_break_scene, observe
from read_counter import recognize
from presenter_sessions import advance_sessions
from test_presenter_sessions import observation
import supervisor

ROOT=Path(__file__).parent/'test-data/2026-10-25-night-break'
BREAKS=['01ab91c9-43ef-4f55-8d1d-244bbea6990c.jpg','81fb9ca9-4b8c-4bc5-92f6-4ac68f315b1e.jpg']


class BreakSceneTests(unittest.TestCase):
    def test_break_frames_match_and_thin_led_counter_is_read(self):
        for name in BREAKS:
            image=Image.open(ROOT/name)
            self.assertTrue(detect_break_scene(image)[0])
            self.assertEqual(recognize(image)[0]['value'],63333)

    def test_every_saved_transition_frame_is_not_a_break(self):
        for path in ROOT.glob('*.jpg'):
            if path.name not in BREAKS:
                self.assertFalse(detect_break_scene(Image.open(path))[0],path.name)

    def test_blank_board_and_obstructed_background_do_not_match(self):
        original=Image.open(ROOT/BREAKS[0]).convert('RGB')
        blank=original.copy()
        ImageDraw.Draw(blank).rectangle((920,480,1325,690),fill=(250,250,250))
        self.assertFalse(detect_break_scene(blank)[0])
        obstruction=original.copy()
        ImageDraw.Draw(obstruction).rectangle((884,162,1344,432),fill='black')
        self.assertFalse(detect_break_scene(obstruction)[0])

    def test_break_never_creates_a_clothing_baseline(self):
        state,status,score,crop,details=observe(Image.open(ROOT/BREAKS[0]),{},0,'break',
                                              (.48,.51,.65,.62),.45,60,30)
        self.assertEqual(status,'night_break_detected')
        self.assertEqual(details['scene_kind'],'scheduled_break')
        self.assertIsNone(crop)
        self.assertEqual(state['views'],{})

    def test_small_boundary_increment_needs_repeated_break_evidence(self):
        cfg=supervisor.config()
        state={'anchor':{'value':63330,'time':0},'rates':[.7]}
        normal,unchanged=supervisor.validate_counter(63333,300,state,cfg)
        self.assertIsNone(normal['accepted'])
        special,updated=supervisor.validate_counter(63333,300,state,cfg,break_confirmed=True)
        self.assertEqual(special['status'],'accepted_break_boundary')
        self.assertEqual(updated['rates'],state['rates'])
        for value in (63329,999999):
            result,_=supervisor.validate_counter(value,300,state,cfg,break_confirmed=True)
            self.assertIsNone(result['accepted'])

    def test_real_pixels_close_prior_stint_without_attributing_last_three_folds(self):
        cfg=supervisor.config()
        cfg.update(interval=30,effective_interval=30,night_idle=180,
                   night_start=time(21,30),night_end=time(6,30),timezone=ZoneInfo('Europe/Warsaw'))
        base=datetime(2026,10,25,21,tzinfo=timezone.utc).timestamp()
        sessions,_,_=advance_sessions({},observation(base-30,63320))
        sessions,_,_=advance_sessions(sessions,observation(base,63330))
        state={'presenters':sessions,'counter':{'anchor':{'value':63330,'time':base},'rates':[.7]}}
        with tempfile.TemporaryDirectory() as temp:
            cfg['data']=Path(temp)
            for index in range(1,10):
                with patch('supervisor.datetime',wraps=datetime) as clock:
                    clock.now.return_value=datetime.fromtimestamp(base+index*30,timezone.utc)
                    obs=supervisor.observation(cfg)
                    state=supervisor.analyze(cfg,state,obs,ROOT/BREAKS[index%2])
                state=json.loads(json.dumps(state))
            self.assertEqual(obs['raw_counter'],63333)
            self.assertNotIn('current',state['presenters'])
            ended=obs['details']['presenter_session_updates'][0]
            self.assertEqual(ended['last_counter'],63330)
            self.assertEqual(ended['ended_at'],observation(base)['observed_at'])
            self.assertEqual(ended['end_evidence']['counter'],63333)
            self.assertTrue(ended['end_evidence']['break_scene_verified'])

    def test_break_sign_with_unreadable_counter_does_not_end_stint(self):
        cfg={'timezone':ZoneInfo('Europe/Warsaw'),'start':time(21,30),'end':time(6,30),
             'seconds':180,'max_gap':75}
        base=datetime(2026,10,25,21,tzinfo=timezone.utc).timestamp()
        state,_,_=advance_sessions({},observation(base,63330))
        for seconds in range(30,300,30):
            obs=observation(base+seconds,None,view='night_break')
            obs['details']['camera_view']['scene_kind']='scheduled_break'
            state,_,_=advance_sessions(state,obs,night_config=cfg)
        self.assertIn('current',state)

    def test_legacy_state_uses_last_attributed_counter_time_not_decoder_recovery(self):
        cfg={'timezone':ZoneInfo('Europe/Warsaw'),'start':time(21,30),'end':time(6,30),
             'seconds':180,'max_gap':75}
        base=datetime(2026,10,25,21,tzinfo=timezone.utc).timestamp()
        state,_,_=advance_sessions({},observation(base,63330))
        # A legacy/unattributed recovery timestamp must not overrule the last
        # counter observation actually assigned to this stint.
        state['current']['last_increase_at']=observation(base+600)['observed_at']
        for seconds in range(600,810,30):
            obs=observation(base+seconds,63333,view='night_break')
            obs['details']['camera_view']['scene_kind']='scheduled_break'
            state,rows,_=advance_sessions(state,obs,night_config=cfg)
        self.assertEqual(rows[0]['ended_at'],observation(base)['observed_at'])
        self.assertEqual(rows[0]['end_evidence']['estimated_stop_basis'],'last_attributed_counter_observation')


if __name__=='__main__':
    unittest.main()
