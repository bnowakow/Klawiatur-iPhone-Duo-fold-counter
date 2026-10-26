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
             at+timedelta(minutes=61), 'sustained_clothing_change', 'start-link', 'end-link', False)]
        rows = export.snapshot(conn, at)
        self.assertEqual(rows[0][:5], ['2026-10-26 07:00:00', 69846, 3843, 60, 64.05])
        self.assertEqual(rows[0][9:12], ['id', 'start-link', 'end-link'])
        self.assertEqual(conn.execute.call_args.args[1], (export.STREAM,))

    def test_missing_counter_is_blank_instead_of_zero_folds(self):
        at = datetime.now(timezone.utc)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            ('id', at, None, None, None, None, None, None, None, None, True)]
        row = export.snapshot(conn, at)[0]
        self.assertEqual(row[1:5], ['', '', '', ''])
        self.assertIn('niepewne', row[8])

    def test_wrong_workbook_missing_tab_and_original_sheet_id_are_rejected(self):
        self.assertEqual(export.target(metadata())['sheetId'], export.TAB_ID)
        for value in ({'spreadsheetId': 'other'},
                      {'spreadsheetId': export.SPREADSHEET_ID, 'sheets': []},
                      {'spreadsheetId': export.SPREADSHEET_ID, 'sheets': [
                          {'properties': {'title': export.TAB, 'sheetId': 0}}]}):
            with self.assertRaises(export.ExportError):
                export.target(value)

    def test_upsert_keeps_row_positions_and_never_touches_review_columns(self):
        existing = [export.HEADERS, source_row('second'), source_row('first')]
        requests = export.plan(PROPS, existing,
                               [source_row('first', 20), source_row('second', 10), source_row('third')], False)
        writes = [r['updateCells'] for r in requests if 'updateCells' in r]
        self.assertEqual([w['start']['rowIndex'] for w in writes], [2, 1, 3])
        self.assertTrue(all(w['start']['sheetId'] == export.TAB_ID for w in writes))
        self.assertTrue(all(len(w['rows'][0]['values']) == 14 for w in writes))
        self.assertTrue(all(w['fields'] == 'userEnteredValue' for w in writes))

    def test_duplicate_or_missing_destination_id_is_rejected(self):
        for existing in ([export.HEADERS, source_row(), source_row()],
                         [export.HEADERS, ['unexpected manual row']]):
            with self.assertRaises(export.ExportError):
                export.plan(PROPS, existing, [source_row()], False)

    def test_fresh_tab_has_ownership_note_and_no_writes_to_human_columns(self):
        requests = export.plan(PROPS, [], [source_row()], True)
        header = next(r['updateCells'] for r in requests if 'updateCells' in r)
        self.assertTrue(header['rows'][0]['values'][0]['note'].startswith(export.MARKER))
        self.assertEqual(len(header['rows'][0]['values']), 14)
        export.validate_requests(requests, export.TAB_ID, True)

    def test_write_guard_blocks_other_tab_review_columns_and_destructive_operations(self):
        invalid = [
            {'deleteSheet': {'sheetId': export.TAB_ID}},
            {'copyPaste': {'source': {'sheetId': 0}, 'destination': {'sheetId': export.TAB_ID}}},
            {'updateCells': {'start': {'sheetId': 0, 'rowIndex': 1, 'columnIndex': 0}, 'rows': []}},
            {'updateCells': {'start': {'sheetId': export.TAB_ID, 'rowIndex': 1, 'columnIndex': 0},
                             'rows': [{'values': [export.cell('x')]*15}]}},
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
        existing = [export.HEADERS, source_row() + ['confirmed', 'human note']]
        with patch.object(export, 'request', return_value=metadata()) as req, \
             patch.object(export, 'read_owned', return_value=(existing, False)):
            result = export.sync('token', [source_row(count=999)])
        self.assertTrue(result['human_columns_preserved'])
        batch = req.call_args.args[2]['requests']
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]['updateCells']['start']['rowIndex'], 1)
        self.assertEqual(len(batch[0]['updateCells']['rows'][0]['values']), 14)

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
