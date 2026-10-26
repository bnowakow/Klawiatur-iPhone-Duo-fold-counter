import unittest
import appearance_descriptions as appearance


class AppearanceTests(unittest.TestCase):
    def test_distinguishes_people_with_same_shirt(self):
        base={'clothing':'A black T-shirt.','hair':'Short brown hair.','headwear':'No hat is visible.',
              'glasses':'Yes, black framed glasses.','facial_hair':'A short beard.',
              'accessories':'A black watch.'}
        first,features=appearance.summarize(base)
        second,_=appearance.summarize(dict(base,hair='Long blonde hair.',glasses='No glasses.',
                                          facial_hair='No facial hair.'))
        self.assertNotEqual(first,second)
        for phrase in ['Czarna koszulka'.lower(),'brązowe','krótkie','okulary','krótka broda','czarny zegarek']:
            self.assertIn(phrase,first)
        self.assertEqual(features['headwear']['state'],'not_visible')
        self.assertNotIn('sex',features)
        self.assertNotIn('gender',features)

    def test_uncertain_is_not_absence(self):
        _,features=appearance.summarize({'clothing':'A black shirt.'})
        for name in ['hair','headwear','glasses','facial_hair','accessories']:
            self.assertEqual(features[name]['state'],'unknown')
        self.assertEqual(appearance.glasses('Cannot tell if glasses are visible.'),{'state':'unknown'})
        self.assertEqual(appearance.glasses('No eyeglasses are visible.'),{'state':'not_visible'})

    def test_visible_hat_and_hair_style(self):
        self.assertEqual(appearance.headwear('A red baseball cap.'),
                         {'state':'visible','type':'czapka z daszkiem','color':'czerwona'})
        self.assertEqual(appearance.hair('Long black curly hair.'),
                         {'state':'visible','color':'czarne','length':'długie','style':'kręcone'})

    def test_facial_hair_negation_does_not_hide_moustache(self):
        result=appearance.facial_hair('No beard but a short moustache.')
        self.assertEqual(result['items'],['krótkie wąsy'])
        self.assertEqual(appearance.facial_hair('Clean-shaven face.'),{'state':'not_visible'})

    def test_unrequested_gender_labels_removed_from_diagnostics(self):
        self.assertEqual(appearance.neutralize('A man has short hair.'),'A person has short hair.')
