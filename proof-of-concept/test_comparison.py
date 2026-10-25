from datetime import datetime, timezone, timedelta
import unittest
from compare_current_tester import published_values,cadence_errors,estimates,InvalidPublishedValues


class ComparisonTests(unittest.TestCase):
    def test_overnight_negative_published_rate_is_unavailable(self):
        parsed={'headers':['Wynik bieżącego zawodnika','Minuta pomiaru','Szacunek na koniec','Złożenia na minutę'],
                'rows':[['2432','-1','-145920','-2432']]}
        with self.assertRaisesRegex(InvalidPublishedValues, '-2432'):
            published_values(parsed)

    def test_polish_headers_and_decimal_comma(self):
        parsed={'headers':['Wynik bieżącego zawodnika','Minuta pomiaru','Szacunek na koniec','Złożenia na minutę'],
                'rows':[['','','',''],['602','15','2408','40,1']]}
        self.assertEqual(published_values(parsed),{'folds':602,'minutes':15,'projection':2408,'fpm':40.1})
        parsed['rows'][1][-1]='NaN'
        with self.assertRaises(ValueError):
            published_values(parsed)

    def test_whole_stint_and_recent_window_are_separate(self):
        t=datetime(2026,10,25,tzinfo=timezone.utc)
        samples=[(t+timedelta(seconds=s),100+s) for s in range(0,301,10)]
        result=estimates(samples,t,100)
        self.assertEqual(result['recent_fpm'],60)
        self.assertEqual(result['stint_fpm'],60)
        self.assertEqual(result['recent_window_seconds'],180)
        self.assertEqual(result['sample_count'],19)

    def test_downsampling_exposes_motion_between_endpoints(self):
        t=datetime(2026,10,25,tzinfo=timezone.utc)
        samples=[(t+timedelta(seconds=s),v) for s,v in [(0,0),(10,0),(20,20),(30,20),(40,40),(50,40),(60,60)]]
        rows=cadence_errors(samples,(10,60))
        self.assertEqual(rows[0]['max_absolute_counter_error'],0)
        self.assertGreater(rows[1]['mean_absolute_counter_error'],0)
        self.assertEqual(rows[1]['retained_samples'],2)

    def test_not_enough_data_stays_unavailable(self):
        self.assertEqual(cadence_errors([]),[])
        t=datetime.now(timezone.utc)
        result=estimates([(t,100)],None,None)
        self.assertIsNone(result['recent_fpm'])
        self.assertIsNone(result['stint_fpm'])

    def test_subsecond_reference_not_invented_from_ten_second_samples(self):
        t=datetime.now(timezone.utc)
        points=[(t+timedelta(seconds=i*10), i*7) for i in range(10)]
        rows=cadence_errors(points)
        self.assertEqual([r['target_interval_seconds'] for r in rows[:5]],[1,2,3,4,5])
        self.assertTrue(all(r['status']=='finer_reference_required' for r in rows[:5]))
        self.assertEqual(rows[5]['status'],'evaluated')
