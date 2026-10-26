from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ipduotest_pl import archive_root, discover, fetch, parse_sheet, collect

FIXTURES = Path(__file__).parent / 'test-data/2026-10-25-ipduotest-pl'


class Response(io.BytesIO):
    def __init__(self, body, url='https://example.test/', content_type='text/html'):
        super().__init__(body)
        self.url = url
        self.headers = Message()
        self.headers['Content-Type'] = content_type


class IpduotestPlTests(unittest.TestCase):
    def test_archive_rename_preserves_pending_runs_and_blobs(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'DATA_DIR': temp}):
            legacy = Path(temp) / 'official'
            (legacy / 'pending').mkdir(parents=True)
            (legacy / 'blobs').mkdir()
            (legacy / 'pending/run.json').write_bytes(b'{"id":"saved"}')
            (legacy / 'blobs/hash').write_bytes(b'original response')
            root = archive_root()
            self.assertEqual(root.name, 'ipduotest-pl')
            self.assertFalse(legacy.exists())
            self.assertEqual((root / 'pending/run.json').read_bytes(), b'{"id":"saved"}')
            self.assertEqual((root / 'blobs/hash').read_bytes(), b'original response')
            self.assertEqual(archive_root(), root)

    def test_archive_rename_refuses_to_overwrite_existing_archive(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ', {'DATA_DIR': temp}):
            for name in ('official', 'ipduotest-pl'):
                (Path(temp) / name).mkdir()
            with self.assertRaisesRegex(RuntimeError, 'Both legacy'):
                archive_root()

    def test_discovery_deduplicates_embeds_and_keeps_table_metadata(self):
        sources = discover((FIXTURES/'home.html').read_text())
        sheets = [s for s in sources if s['kind']=='sheet']
        self.assertEqual(len(sheets),3)
        self.assertEqual(len(sources),4)
        schedule = next(s for s in sheets if s['source_url'].endswith('gid=0'))
        self.assertEqual({t['id'] for t in schedule['metadata']['tables']},{1,2})

    def test_complete_real_table_retains_notes_future_slots_and_empty_rows(self):
        result = parse_sheet((FIXTURES/'sheet-1.html').read_text())
        self.assertEqual(result['row_count'],42)
        self.assertEqual(result['nonempty_row_count'],31)
        self.assertEqual(next(row for row in result['rows'] if any(row))[0],'2026-10-24 14:28:00')
        self.assertTrue(any('KURZ' in cell for row in result['rows'] for cell in row))
        future = next(r for r in result['rows'] if r[0]=='2026-10-25 21:00:00')
        self.assertEqual(future[1],'')
        self.assertEqual(future[4],'0,0')
        self.assertEqual(result['headers'][0],'Data / Start Testera')

    def test_current_score_and_top5_preserve_source_format(self):
        current = parse_sheet((FIXTURES/'sheet-4.html').read_text())
        self.assertEqual(current['rows'][0],['2237','57','2355','39'])
        top = parse_sheet((FIXTURES/'sheet-3.html').read_text())
        self.assertEqual(top['rows'][0][3],'56,7')
        self.assertEqual(top['row_count'],7)

    def test_cell_metadata_and_google_chrome(self):
        result = parse_sheet('<table class="waffle"><tr><th>A</th></tr>'
                             '<tr><th>1</th><td>Header</td></tr><tr><th>2</th>'
                             '<td colspan="2"><a href="https://example.test">A &amp; B</a>'
                             '<br>next<img src="https://example.test/image.png"></td></tr></table>')
        self.assertEqual(result['rows'],[['A & B\nnext']])
        self.assertEqual(result['cells'][1][0]['links'],['https://example.test'])
        self.assertEqual(result['cells'][1][0]['colspan'],2)

    def test_parse_failure_preserves_raw_response_and_checksum(self):
        source={'resource_key':'sheet:test','source_url':'https://example.test', 'kind':'sheet','metadata':{}}
        with tempfile.TemporaryDirectory() as temp, patch('ipduotest_pl.urlopen', return_value=Response(b'<html>login</html>')):
            resource=fetch(source,Path(temp))
            self.assertEqual(resource['status'],'error')
            self.assertIsNotNone(resource['sha256'])
            self.assertEqual((Path(temp)/'blobs'/resource['sha256']).read_bytes(),b'<html>login</html>')
            self.assertEqual(resource['sha256'],hashlib.sha256(b'<html>login</html>').hexdigest())

    def test_failed_homepage_is_durable_and_does_not_look_complete(self):
        with tempfile.TemporaryDirectory() as temp, patch('ipduotest_pl.urlopen', side_effect=TimeoutError('offline')):
            root=Path(temp)
            run=collect(root)
            self.assertEqual(run['status'],'error')
            self.assertIsNotNone(run['discovery_error'])
            saved=json.loads((root/'pending'/(run['id']+'.json')).read_text())
            self.assertEqual(saved['resources'][0]['status'],'error')

    def test_missing_table_config_is_an_explicit_discovery_error(self):
        with self.assertRaisesRegex(ValueError,'No published'):
            discover('<html>Changed layout</html>')
