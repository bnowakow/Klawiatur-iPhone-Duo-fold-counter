from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import unittest
from PIL import Image
from camera_views import observe
from presenter_sessions import advance_appearances,advance_sessions

ROOT=Path(__file__).parent/'test-data/2026-10-26-clothing-occlusion'


class ClothingOcclusionTests(unittest.TestCase):
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
