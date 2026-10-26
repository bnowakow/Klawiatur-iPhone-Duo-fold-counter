from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from PIL import Image
from camera_views import observe
from presenter_sessions import advance_appearances,advance_sessions

ROOT=Path(__file__).parent/'test-data/2026-10-26-clothing-occlusion'


class ClothingOcclusionTests(unittest.TestCase):
    def test_light_to_dark_shirt_still_requires_a_handover(self):
        root = ROOT/'raised-arms'
        with Image.open(root/'baseline.jpg') as image:
            state, _, _, _, _ = observe(image, {}, 0, 'old', (.48,.51,.65,.62), .45, 60, 5.9)
        with Image.open(ROOT.parent/'2026-10-26-dark-clothing-handover/first-new-main.jpg') as image:
            for ts in range(6, 73, 6):
                state, status, _, _, camera = observe(image, state, ts, str(ts), (.48,.51,.65,.62), .45, 60, 5.9)
        self.assertEqual(status, 'possible_handover_review_required')
        self.assertGreaterEqual(camera['shoulder_score'], .45)

    def test_upgrade_uses_original_shoulder_and_requires_original_frame(self):
        root = ROOT/'raised-arms'
        with Image.open(root/'baseline.jpg') as image:
            state, _, _, _, _ = observe(image, {}, 0, 'old', (.48,.51,.65,.62), .45, 60, 5.9)
        saved = state['views']['presenter_main']
        original = saved.pop('shoulder_baseline')
        saved.pop('shoulder_roi')
        with Image.open(root/'7d3dceb5-6bbc-4d40-804a-fd8441dd1ecb.jpg') as image:
            _, status, _, _, _ = observe(image, state, 6, 'new', (.48,.51,.65,.62), .45, 60, 5.9)
            self.assertEqual(status, 'clothing_baseline_review_required')
            with tempfile.TemporaryDirectory() as temp:
                frame = Path(temp)/'old/frame.jpg'
                frame.parent.mkdir()
                shutil.copyfile(root/'baseline.jpg', frame)
                upgraded, status, _, _, camera = observe(image, state, 6, 'new', (.48,.51,.65,.62), .45, 60, 5.9, evidence_root=temp)
        self.assertEqual(status, 'no_clothing_change_detected')
        self.assertEqual(upgraded['views']['presenter_main']['shoulder_baseline'], original)
        self.assertTrue(camera['arm_occlusion_ignored'])

    def test_raised_arm_cues_do_not_freeze_the_beige_shirt_stint(self):
        root = ROOT/'raised-arms'
        rows = json.loads((root/'manifest.json').read_text())['frames']
        clothing, appearances, sessions = {}, {}, {}
        ignored = 0
        for row in rows:
            with Image.open(root/row['file']) as image:
                clothing, status, _, _, camera = observe(image, clothing,
                    datetime.fromisoformat(row['observed_at']).timestamp(), row['id'],
                    (.48,.51,.65,.62), .45, 60, 5.9)
            self.assertIn(status, ('baseline_created','no_clothing_change_detected'), (row['id'],camera))
            ignored += bool(camera.get('arm_occlusion_ignored'))
            obs = dict(row, stream_url='fixture', clothing_status=status, details={'camera_view':camera})
            appearances, _ = advance_appearances(appearances, obs)
            self.assertFalse(appearances.get('attribution_review_required'))
            obs['details']['presenter_attribution_review_required'] = False
            sessions, _, promoted = advance_sessions(sessions, obs)
            if row is rows[0]:
                stint = sessions['current']['id']
            self.assertEqual(sessions['current']['id'], stint)
            self.assertFalse(promoted)
            self.assertEqual(sessions['current']['last_counter'], row['accepted_counter'])
        self.assertEqual(ignored, 2)

    def test_all_reviewed_phone_hand_obstructions_keep_the_same_stint(self):
        fixtures=json.loads((ROOT/'manifest.json').read_text())
        base=datetime(2026,10,26,6,tzinfo=timezone.utc)
        clothing,sessions,appearances={},{},{}
        followup=json.loads((ROOT/'followup-manifest.json').read_text())['frames']
        entries=[{'file':'baseline.jpg','accepted_counter':66003,'observed_at':base.isoformat()}]+fixtures['frames']+followup
        ignored=0
        for index,row in enumerate(entries):
            stamp=datetime.fromisoformat(row['observed_at']).timestamp()
            with Image.open(ROOT/row['file']) as image:
                clothing,status,score,_,camera=observe(image,clothing,stamp,str(index),(.48,.51,.65,.62),.45,60,4.9)
            self.assertIn(status,('baseline_created','no_clothing_change_detected'),(row['file'],camera))
            ignored+=bool(camera.get('localized_garment_change_ignored'))
            obs={'id':str(index),'stream_url':'fixture','observed_at':row['observed_at'],
                 'accepted_counter':row['accepted_counter'],'clothing_status':status,'details':{'camera_view':camera}}
            appearances,_=advance_appearances(appearances,obs)
            self.assertFalse(appearances.get('attribution_review_required'))
            obs['details']['presenter_attribution_review_required']=False
            sessions,_,promoted=advance_sessions(sessions,obs)
            if index==0:session=sessions['current']['id']
            self.assertEqual(sessions['current']['id'],session)
            self.assertFalse(promoted)
            if row['accepted_counter'] is not None:
                self.assertEqual(sessions['current']['last_counter'],row['accepted_counter'])
        self.assertGreaterEqual(ignored,10)
