from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import sheets_auth as auth
import sheets_export as export


PROPS = {'title': export.TAB, 'sheetId': export.TAB_ID,
         'gridProperties': {'rowCount': 1000, 'columnCount': 26}}


def metadata():
    return {'spreadsheetId': export.SPREADSHEET_ID, 'sheets': [
        {'properties': {'sheetId': 0, 'title': 'Ranking'}}, {'properties': PROPS}]}


def source_row(sid='stint-1', count=42):
    row = [''] * export.OWNED_COLUMNS
    row[2], row[export.ID_COLUMN] = count, sid
    return row


class SheetsExportTests(unittest.TestCase):
    def test_ranking_columns_are_first_and_in_original_order(self):
        self.assertEqual(export.HEADERS[:5], ['Data / Start Testera', 'Wynik na koniec',
                         'Wynik indywidualny', 'Czas składania (min)', 'Złożenia na minutę'])

    def test_snapshot_counts_baseline_and_real_minutes(self):
        at = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            ('id', at, at, 66003, at+timedelta(minutes=60), 69846,
             at+timedelta(minutes=61), 'sustained_clothing_change', 'start-link', 'end-link', False, 'Ubiór: czarna koszulka.', 'sustained_clothing_change', at)]
        rows = export.snapshot(conn, at)
        self.assertEqual(rows[0][:5], ['2026-10-26 07:00:00', 69846, 3843, 60, 64.05])
        self.assertEqual(rows[0][9:12], ['id', 'start-link', 'end-link'])
        self.assertEqual(rows[0][-1], 'Ubiór: czarna koszulka.')
        self.assertEqual(conn.execute.call_args.args[1], (export.STREAM,))

    def test_missing_counter_is_blank_instead_of_zero_folds(self):
        at = datetime.now(timezone.utc)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            ('id', at, None, None, None, None, None, None, None, None, True, None, 'first_observed', None)]
        self.assertEqual(export.snapshot(conn, at), [])

    def test_confirmed_early_freeze_finish_replaces_the_old_partial_sheet_row(self):
        at = datetime(2026, 10, 26, 7, 59, 45, tzinfo=timezone.utc)
        baseline = at+timedelta(seconds=7)
        finish = at+timedelta(minutes=49, seconds=58)
        sid = 'bcec84c2-0a6a-4ab2-a779-58fdd2058f6d'
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            (sid, at, baseline, 72869, finish, 75000, finish,
             'confirmed_freeze_test_pause', 'start-link', '', False,
             'Ubiór: koszula w kratę.', 'sustained_clothing_change', baseline+timedelta(seconds=7))]
        rows = export.snapshot(conn, finish)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:3], [75000, 2131])
        self.assertEqual(rows[0][5], '2026-10-26 09:49:43')
        self.assertIn('confirmed_freeze_test_pause', rows[0][8])
        existing = [[export.SOURCE_TEXT], export.HEADERS, source_row(sid, 73417)+['human review']]
        changes = export.plan(metadata()['sheets'][1]['properties'], existing, rows, False)
        updates = [r['updateCells'] for r in changes if 'updateCells' in r
                   and r['updateCells'].get('start', {}).get('rowIndex') == 2]
        self.assertTrue(updates)
        self.assertEqual(updates[0]['rows'][0]['values'][1]['userEnteredValue']['numberValue'], 75000)

    def test_complete_run_requires_captured_handover_start_and_real_finish(self):
        at = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
        finish = at+timedelta(hours=1)
        args = dict(reason='sustained_clothing_change', started=at+timedelta(seconds=5),
                    baseline_at=at, baseline=1000, last_at=finish, last=4000,
                    ended=finish+timedelta(seconds=5), end_reason='sustained_clothing_change', appeared=at)
        self.assertTrue(export.complete_run(**args))
        self.assertTrue(export.complete_run(**dict(args, reason='reviewed_handover_correction')))
        self.assertTrue(export.complete_run(**dict(args, end_reason='night_break_sign_counter_stopped_review_required')))
        for changes in [dict(reason='first_observed'), dict(reason='manual_baseline_reset'),
                        dict(reason='activity_resumed_after_night_pause'), dict(started=None),
                        dict(ended=None), dict(end_reason='manual_baseline_reset'),
                        dict(baseline=None), dict(last=900), dict(last_at=at),
                        dict(ended=at+timedelta(seconds=10)),
                        dict(appeared=at-timedelta(minutes=2))]:
            with self.subTest(changes=changes):
                self.assertFalse(export.complete_run(**dict(args, **changes)))

    def test_excluded_rows_are_hidden_without_clearing_results_or_reviewer_notes(self):
        existing = [[export.SOURCE_TEXT], export.HEADERS,
                    source_row('partial')+['human review'], source_row('complete')]
        requests = export.plan(PROPS, existing, [source_row('complete')], False)
        visibility = [r['updateDimensionProperties'] for r in requests
                      if 'updateDimensionProperties' in r and r['updateDimensionProperties']['fields']=='hiddenByUser']
        self.assertEqual([(r['range']['startIndex'], r['properties']['hiddenByUser']) for r in visibility],
                         [(2, True), (3, False)])
        data_writes = [r['updateCells'] for r in requests if 'updateCells' in r
                       and r['updateCells']['fields']=='userEnteredValue']
        self.assertEqual([r['start']['rowIndex'] for r in data_writes], [3])
        for invalid in [export.row_visibility(export.TAB_ID, 0, True),
                        export.row_visibility(0, 2, True)]:
            with self.assertRaises(export.ExportError):
                export.validate_requests([invalid], export.TAB_ID, False)

    def test_completed_handover_with_late_baseline_is_exported_as_partial(self):
        at = datetime(2026, 10, 26, 7, tzinfo=timezone.utc)
        baseline = at+timedelta(seconds=105)
        last = at+timedelta(minutes=59)
        conn = MagicMock()
        row = ('late', at, baseline, 69900, last, 72857,
               last+timedelta(seconds=25), 'sustained_clothing_change',
               'start-link', 'end-link', False, 'Ubiór: beżowa koszulka.',
               'sustained_clothing_change', baseline+timedelta(seconds=5))
        conn.execute.return_value.fetchall.return_value = [row]
        result = export.snapshot(conn, at)
        self.assertEqual(result[0][2], 2957)
        self.assertIn('WYNIK CZĘŚCIOWY', result[0][8])
        self.assertIn('105 s', result[0][8])
        self.assertIn('nieuwzględnione', result[0][8])
        conn.execute.return_value.fetchall.return_value = [row[:6]+(None,)+row[7:]]
        self.assertEqual(export.snapshot(conn, at), [])

    def test_source_row_is_static_above_headers(self):
        changes = export.plan(PROPS, [], [source_row()], True)
        writes = [r['updateCells'] for r in changes if 'updateCells' in r]
        self.assertEqual(writes[0]['start']['rowIndex'], 0)
        self.assertEqual(writes[0]['rows'][0]['values'][0]['userEnteredValue']['stringValue'], export.SOURCE_TEXT)
        self.assertEqual(writes[1]['start']['rowIndex'], 1)
        self.assertEqual(writes[2]['start']['rowIndex'], 2)

    def test_source_notice_updates_in_place_without_shifting_data_or_review_rows(self):
        existing = [[export.LEGACY_SOURCE_TEXT], export.HEADERS, source_row()]
        requests = export.plan(PROPS, existing, [source_row()], False)
        self.assertFalse(any('insertDimension' in req for req in requests))
        source = next(req['updateCells'] for req in requests if 'updateCells' in req
                      and req['updateCells']['start']['rowIndex']==0)
        value = source['rows'][0]['values'][0]
        self.assertEqual(value['userEnteredValue']['stringValue'], export.SOURCE_TEXT)
        self.assertEqual(value['textFormatRuns'][0]['startIndex'], len(export.SOURCE_PREFIX))

    def test_legacy_migration_preserves_human_notes_and_row_attachment(self):
        # Existing O/P are reviewer-owned. Simulate Sheets' atomic insertion
        # and value updates, then verify both manual cells moved with the stint.
        old = source_row('old')[:14] + ['verified', 'manual note']
        grid = [export.LEGACY_HEADERS[:] + ['Review', 'Notes'], old[:]]
        changes = export.plan(PROPS, [export.LEGACY_HEADERS, old[:14]], [source_row('old')], False)
        for req in changes:
            if 'insertDimension' in req:
                area = req['insertDimension']['range']
                if area['dimension'] == 'ROWS':
                    grid.insert(area['startIndex'], ['']*len(grid[0]))
                else:
                    for row in grid:
                        row.insert(area['startIndex'], '')
            elif 'updateCells' in req:
                update = req['updateCells']
                index, col = update['start']['rowIndex'], update['start']['columnIndex']
                while len(grid) <= index:
                    grid.append(['']*17)
                for cell_index, value in enumerate(update['rows'][0]['values']):
                    if 'userEnteredValue' in value:
                        entered = value['userEnteredValue']
                        grid[index][col+cell_index] = entered.get('stringValue', entered.get('numberValue', ''))
        self.assertEqual(grid[0][0], export.SOURCE_TEXT)
        self.assertEqual(grid[1][:15], export.HEADERS)
        self.assertEqual(grid[2][9], 'old')
        self.assertEqual(grid[1][15:17], ['Review', 'Notes'])
        self.assertEqual(grid[2][15:17], ['verified', 'manual note'])
        # A repeat sync updates existing rows without reinserting either axis.
        repeat = export.plan(PROPS, grid, [source_row('old')], False)
        self.assertFalse(any('insertDimension' in req for req in repeat))

    def test_fold_counts_are_integer_formatted_without_dangling_decimal_separator(self):
        changes = export.plan(PROPS, [[export.SOURCE_TEXT], export.HEADERS, source_row()], [source_row()], False)
        formats = [r['repeatCell'] for r in changes if 'repeatCell' in r]
        counts = next(r for r in formats if r['range']['startColumnIndex'] == 1)
        self.assertEqual(counts['range']['endColumnIndex'], 3)
        self.assertEqual(counts['range']['startRowIndex'], 2)
        self.assertEqual(counts['cell']['userEnteredFormat']['numberFormat']['pattern'], '0')

    def test_wrong_workbook_missing_tab_and_original_sheet_id_are_rejected(self):
        self.assertEqual(export.target(metadata())['sheetId'], export.TAB_ID)
        for value in ({'spreadsheetId': 'other'},
                      {'spreadsheetId': export.SPREADSHEET_ID, 'sheets': []},
                      {'spreadsheetId': export.SPREADSHEET_ID, 'sheets': [
                          {'properties': {'title': export.TAB, 'sheetId': 0}}]}):
            with self.assertRaises(export.ExportError):
                export.target(value)

    def test_upsert_keeps_row_positions_and_never_touches_review_columns(self):
        existing = [[export.SOURCE_TEXT], export.HEADERS, source_row('second'), source_row('first')]
        requests = export.plan(PROPS, existing,
                               [source_row('first', 20), source_row('second', 10), source_row('third')], False)
        writes = [r['updateCells'] for r in requests if 'updateCells' in r and r['updateCells']['fields'] == 'userEnteredValue']
        self.assertEqual([w['start']['rowIndex'] for w in writes], [3, 2, 4])
        self.assertTrue(all(w['start']['sheetId'] == export.TAB_ID for w in writes))
        self.assertTrue(all(len(w['rows'][0]['values']) == export.OWNED_COLUMNS for w in writes))
        self.assertTrue(all(w['fields'] == 'userEnteredValue' for w in writes))

    def test_duplicate_or_missing_destination_id_is_rejected(self):
        for existing in ([export.HEADERS, source_row(), source_row()],
                         [export.HEADERS, ['unexpected manual row']]):
            with self.assertRaises(export.ExportError):
                export.plan(PROPS, existing, [source_row()], False)

    def test_fresh_tab_has_ownership_note_and_no_writes_to_human_columns(self):
        requests = export.plan(PROPS, [], [source_row()], True)
        header = next(r['updateCells'] for r in requests if 'updateCells' in r and r['updateCells']['start']['rowIndex'] == export.HEADER_ROW)
        self.assertTrue(header['rows'][0]['values'][0]['note'].startswith(export.MARKER))
        self.assertEqual(len(header['rows'][0]['values']), export.OWNED_COLUMNS)
        export.validate_requests(requests, export.TAB_ID, True)

    def test_write_guard_blocks_other_tab_review_columns_and_destructive_operations(self):
        invalid = [
            {'deleteSheet': {'sheetId': export.TAB_ID}},
            {'copyPaste': {'source': {'sheetId': 0}, 'destination': {'sheetId': export.TAB_ID}}},
            {'updateCells': {'start': {'sheetId': 0, 'rowIndex': 1, 'columnIndex': 0}, 'rows': []}},
            {'updateCells': {'start': {'sheetId': export.TAB_ID, 'rowIndex': 1, 'columnIndex': 0},
                             'rows': [{'values': [export.cell('x')]*(export.OWNED_COLUMNS+1)}]}},
        ]
        for req in invalid:
            with self.assertRaises(export.ExportError):
                export.validate_requests([req], export.TAB_ID, False)

    def test_formula_like_text_is_written_as_text(self):
        self.assertEqual(export.cell('=IMPORTXML("url")'),
                         {'userEnteredValue': {'stringValue': '=IMPORTXML("url")'}})

    def test_existing_formulas_without_ownership_marker_are_not_overwritten(self):
        response = {'spreadsheetId': export.SPREADSHEET_ID, 'sheets': [
            {'properties': PROPS, 'data': [{'rowData': [{'values': [
                {'userEnteredValue': {'formulaValue': '=1+1'}}]}]}]}]}
        with patch.object(export, 'request', return_value=response), self.assertRaises(export.ExportError):
            export.read_owned('token', PROPS)

    def test_retry_updates_same_row_and_keeps_manual_notes(self):
        existing = [[export.SOURCE_TEXT], export.HEADERS, source_row() + ['confirmed', 'human note']]
        with patch.object(export, 'request', return_value=metadata()) as req, \
             patch.object(export, 'read_owned', return_value=(existing, False)):
            result = export.sync('token', [source_row(count=999)])
        self.assertTrue(result['human_columns_preserved'])
        batch = [r for r in req.call_args.args[2]['requests'] if 'updateCells' in r and r['updateCells']['fields'] == 'userEnteredValue']
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]['updateCells']['start']['rowIndex'], 2)
        self.assertEqual(len(batch[0]['updateCells']['rows'][0]['values']), export.OWNED_COLUMNS)

    def test_tab_change_between_read_and_write_aborts(self):
        changed = metadata()
        changed['sheets'][1] = {'properties': dict(PROPS, sheetId=456)}
        with patch.object(export, 'request', side_effect=[metadata(), changed]) as req, \
             patch.object(export, 'read_owned', return_value=([], True)), \
             self.assertRaises(export.ExportError):
            export.sync('token', [source_row()])
        self.assertEqual(req.call_count, 2)

    def test_http_errors_do_not_expose_tokens_or_remote_content(self):
        with patch.object(export, 'urlopen', side_effect=HTTPError('private', 403, 'private', {}, None)), \
             self.assertRaises(export.ExportError) as raised:
            export.request('private-token')
        self.assertNotIn('private', str(raised.exception))


class SheetsAuthTests(unittest.TestCase):
    def test_credentials_are_private_and_separate_from_youtube(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'SHEETS_AUTH_DIR': folder}):
            with auth.locked():
                auth.save({'project_id': 'p', 'credentials': {'token': 'secret'}})
                self.assertEqual((Path(folder)/'credentials.json').stat().st_mode & 0o777, 0o600)
                self.assertTrue(auth.disconnect()['removed_locally'])
                self.assertFalse((Path(folder)/'credentials.json').exists())

    def test_sheets_grant_is_required(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'SHEETS_AUTH_DIR': folder}):
            auth.save({'project_id': 'p', 'credentials': {'scopes': ['youtube-scope']}})
            with self.assertRaises(auth.AuthorizationRequired):
                auth.token('p')


if __name__ == '__main__':
    unittest.main()
