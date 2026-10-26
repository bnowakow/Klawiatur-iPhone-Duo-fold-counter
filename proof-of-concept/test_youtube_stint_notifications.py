from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from youtube_stint_notifications import finished_message, END_REASONS
from youtube_bot import DISCLAIMER


class FinishedMessageTests(unittest.TestCase):
    def test_closed_stint_wording_statistics_and_exact_disclaimer(self):
        text=finished_message('44.68',2014,DISCLAIMER)
        self.assertTrue(text.startswith('Koniec zmiany.'))
        self.assertNotIn('aktualnej osoby',text)
        self.assertIn('2014',text)
        self.assertIn('44,68',text)
        self.assertIn('2681',text)
        self.assertTrue(text.endswith(DISCLAIMER))
        self.assertLessEqual(len(text),200)

    def test_invalid_final_data_never_becomes_a_message(self):
        for pace,folds in [(None,5),('NaN',5),(30,None),(30,-1)]:
            with self.assertRaises(ValueError):
                finished_message(pace,folds,DISCLAIMER)
        self.assertNotIn('manual_baseline_reset',END_REASONS)


if __name__=='__main__':
    unittest.main()
