import base64
import io
import unittest
from unittest.mock import patch
from PIL import Image
import clothing_descriptions as clothing


class ClothingDescriptionTests(unittest.TestCase):
    def sample(self, view='presenter_main'):
        return {'id':'observation', 'observed_at':'2026-10-26T09:00:00+00:00',
                'details': {'camera_view':{'view':view}}}, {'current': {
                    'id':'session', 'confirmed_at':'now', 'folding_started_at':'now'}}

    def test_each_view_produces_valid_crop_once(self):
        for view in ['presenter_main','presenter_inset']:
            obs, sessions = self.sample(view)
            clothing.prepare(Image.new('RGB',(1920,1080),'white'),obs,sessions)
            job = obs['details']['clothing_description_job']
            crop = Image.open(io.BytesIO(base64.b64decode(job['image_base64'])))
            self.assertGreater(crop.width,100)
            self.assertEqual(job['session_id'],'session')
            self.assertEqual(job['camera_view'],view)
            next_obs, _ = self.sample(view)
            clothing.prepare(Image.new('RGB',(1920,1080),'white'),next_obs,sessions)
            self.assertNotIn('clothing_description_job',next_obs['details'])

    def test_no_caption_before_folding_or_during_uncertainty(self):
        for condition in ['no_folds','unconfirmed','candidate','review','break','unknown']:
            obs, sessions = self.sample()
            if condition == 'no_folds': sessions['current']['folding_started_at'] = None
            if condition == 'unconfirmed': sessions['current']['confirmed_at'] = None
            if condition == 'candidate': sessions['candidate'] = {'id':'other'}
            if condition == 'review': obs['details']['presenter_attribution_review_required'] = True
            if condition == 'break': obs['details']['camera_view']['scene_kind'] = 'scheduled_break'
            if condition == 'unknown': obs['details']['camera_view']['view'] = None
            clothing.prepare(Image.new('RGB',(1920,1080)),obs,sessions)
            self.assertNotIn('clothing_description_job',obs['details'],condition)

    def test_second_session_gets_own_job(self):
        for identifier in ['first','second']:
            obs, sessions = self.sample()
            sessions['current']['id'] = identifier
            clothing.prepare(Image.new('RGB',(1920,1080)),obs,sessions)
            self.assertEqual(obs['details']['clothing_description_job']['session_id'],identifier)

    def test_vision_response_validation(self):
        for body in [b'{"done":false,"response":"white shirt"}', b'{"done":true,"response":""}']:
            with patch('clothing_descriptions.urlopen') as fetch:
                fetch.return_value.__enter__.return_value = io.BytesIO(body)
                with self.assertRaises(ValueError):
                    clothing.generate(b'image','http://vision:11434','model')
        with patch('clothing_descriptions.urlopen') as fetch:
            fetch.return_value.__enter__.return_value = io.BytesIO(
                b'{"done":true,"response":"White short-sleeved T-shirt.","model":"model"}')
            text, metadata = clothing.generate(b'image','http://vision:11434','model')
            self.assertEqual(text,'White short-sleeved t-shirt.')
            payload = __import__('json').loads(fetch.call_args.args[0].data)
            self.assertEqual(payload['images'],[base64.b64encode(b'image').decode()])
            self.assertEqual(payload['keep_alive'],0)
            self.assertEqual(metadata['model'],'model')


class ClothingOnlyTests(unittest.TestCase):
    def test_accessories_and_facial_details_are_excluded(self):
        self.assertEqual(clothing.clothing_only('The person is wearing a grey sweater and a beard.'),
                         'Grey sweater.')
        self.assertEqual(clothing.clothing_only('A white shirt and a black watch.'), 'White shirt.')

    def test_types_and_colors_are_preserved(self):
        self.assertEqual(clothing.clothing_only('A dark green hoodie and blue jeans.'),
                         'Dark green hoodie.')
        self.assertEqual(clothing.clothing_only('A white short-sleeved T-shirt.'),
                         'White short-sleeved t-shirt.')

    def test_no_garment_or_unclear_view_is_explicit(self):
        for text in ['A silver ring and black watch.', 'The shirt is not clearly visible.']:
            self.assertEqual(clothing.clothing_only(text), 'Clothing not clearly visible.')
