"""Export anonymous stints to one fixed tab; preserve human review columns."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import sheets_auth as auth

SPREADSHEET_ID = '1GrqBCt6WzAPBa_mLNPHODPIJkem51wLg5cCatnYw9BM'
TAB = 'Klawiatur-iPhone-Duo-fold-counter'
TAB_ID = 2091633028
BASE = f'https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}'
STREAM = 'https://www.youtube.com/watch?v=vaiHWiExDXg'
SOURCE_URL = 'https://github.com/bnowakow/Klawiatur-iPhone-Duo-fold-counter'
LEGACY_SOURCE_TEXT = 'źródło danych: ' + SOURCE_URL
SOURCE_PREFIX = 'dane generowane przez aplikację mogą posiadać błędy, do weryfikacji. źródło danych: '
SOURCE_TEXT = SOURCE_PREFIX + SOURCE_URL
HEADER_ROW = 1
HEADERS = ['Data / Start Testera', 'Wynik na koniec', 'Wynik indywidualny',
           'Czas składania (min)', 'Złożenia na minutę', 'Koniec zmiany',
           'Licznik początkowy', 'Czas ostatniego odczytu', 'Status danych',
           'ID zmiany', 'Nagranie — start', 'Nagranie — koniec',
           'Aktualizacja', 'Początek pomiaru', 'opis osoby składającej']
LEGACY_HEADERS = HEADERS[:-1]
REVIEW_HEADERS = ['Weryfikacja człowieka', 'Uwagi weryfikatora', 'Zweryfikował(a)']
OWNED_COLUMNS = len(HEADERS)
ID_COLUMN = 9
LEGACY_MARKER = 'Managed by Klawiatur-iPhone-Duo-fold-counter; columns A:N only.'
MARKER = 'Managed by Klawiatur-iPhone-Duo-fold-counter; columns A:O only.'
NOTE = (MARKER + '\nCzasy: Europe/Warsaw. Automatyczne szacunki do weryfikacji. '
        'Minuty i tempo dotyczą okresu od początku pomiaru do ostatniego odczytu. '
        'Widoczne są zakończone zmiany z uchwyconym początkiem i końcem. '
        'WYNIK CZĘŚCIOWY oznacza opóźniony pierwszy odczyt; nie jest pełnym wynikiem osoby. '
        'Możliwe braki przy zmianie osób; granice pozostają szacunkami. '
        'Opis pochodzi z zapisanego automatycznego opisu wyglądu, nie identyfikuje osoby. '
        'Kolumny P i dalsze należą do weryfikatorów.')


class ExportError(ValueError):
    pass


def stamp(value):
    return value.astimezone(ZoneInfo('Europe/Warsaw')).strftime('%Y-%m-%d %H:%M:%S') if value else ''


def captured_run(reason, started, baseline_at, baseline, last_at, last, ended, end_reason, appeared):
    """An observed/reviewed handover brackets the start, not a first mid-run frame."""
    if reason not in ('sustained_clothing_change', 'reviewed_handover_correction'):
        # First observations, manual resets and the first frame on returning
        # from a night pause do not establish the person's true starting boundary.
        return False
    if end_reason not in ('sustained_clothing_change', 'reviewed_handover_correction',
                          'night_break_sign_counter_stopped_review_required',
                          'night_pause_counter_stopped_review_required',
                          'confirmed_freeze_test_pause'):
        return False
    if any(v is None for v in (started, baseline_at, baseline, last_at, last, ended, appeared)):
        return False
    return appeared <= baseline_at <= started <= last_at <= ended and last_at > baseline_at and last >= baseline


def complete_run(reason, started, baseline_at, baseline, last_at, last, ended, end_reason, appeared):
    return baseline_at == appeared and captured_run(
        reason, started, baseline_at, baseline, last_at, last, ended, end_reason, appeared)


def snapshot(conn, at=None):
    """One consistent read, filtered to the production stream; no source-site rows."""
    at = at or datetime.now(timezone.utc)
    rows = conn.execute("""
        SELECT s.id,s.detected_at,s.baseline_counter_at,s.baseline_counter,
               s.last_counter_at,s.last_counter,s.ended_at,s.end_reason,
               a.review_url,f.review_url,
               EXISTS (SELECT FROM observations o WHERE o.stream_url=s.stream_url
                       AND o.details->>'presenter_session_id'=s.id::text
                       AND o.details->>'presenter_attribution_review_required'='true'
                       AND o.details->'presenter_review_resolution' IS NULL),
               CASE WHEN d.description_language='pl' AND d.status='completed'
                    THEN d.description ELSE NULL END,
               s.detection_reason,s.folding_started_at
        FROM presenter_sessions s
        LEFT JOIN presenter_video_events a ON a.session_id=s.id AND a.event_kind='appearance_start'
        LEFT JOIN presenter_video_events f ON f.session_id=s.id AND f.event_kind='folding_finish'
        LEFT JOIN presenter_clothing_descriptions d ON d.session_id=s.id
        WHERE s.stream_url=%s ORDER BY s.detected_at,s.id
    """, (STREAM,)).fetchall()
    output = []
    for sid, appeared, baseline_at, baseline, last_at, last, ended, reason, start_url, end_url, review, description, start_reason, started in rows:
        if not captured_run(start_reason, started, baseline_at, baseline, last_at, last, ended, reason, appeared):
            continue
        folds = last-baseline if last is not None and baseline is not None and last >= baseline else None
        minutes = (last_at-baseline_at).total_seconds()/60 if last_at and baseline_at else None
        pace = folds/minutes if folds is not None and minutes and minutes > 0 else None
        status = 'Zakończona — uchwycony początek i koniec; do weryfikacji'
        if baseline_at > appeared:
            delay = round((baseline_at-appeared).total_seconds())
            status = (f'Zakończona — WYNIK CZĘŚCIOWY; pierwszy odczyt po {delay} s '
                      'od zmiany osoby; wcześniejsze złożenia nieuwzględnione; do weryfikacji')
        if folds is None:
            status += '; brak zaakceptowanych odczytów'
        if review:
            status += '; niepewne przypisanie złożeń'
        if reason:
            status += '; ' + reason
        output.append([stamp(appeared), last if last is not None else '',
                       folds if folds is not None else '',
                       round(minutes, 2) if minutes is not None and minutes >= 0 else '',
                       round(pace, 2) if pace is not None else '', stamp(ended),
                       baseline if baseline is not None else '', stamp(last_at), status,
                       str(sid), start_url or '', end_url or '', stamp(at), stamp(baseline_at), description or ''])
    return output


def request(access_token, suffix='', payload=None):
    """Only the fixed spreadsheet endpoint; never log tokens or response bodies."""
    if suffix not in ('', ':getByDataFilter', ':batchUpdate'):
        raise ExportError('Unsupported Sheets operation')
    url = BASE + suffix
    if not suffix:
        url += '?' + urlencode({'fields': 'spreadsheetId,sheets.properties'})
    req = Request(url, data=json.dumps(payload, allow_nan=False).encode() if payload is not None else None,
                  headers={'Authorization': 'Bearer '+access_token, 'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise ExportError(f'Sheets API returned HTTP {exc.code}; check API access, consent and editor permission') from None
    except (URLError, TimeoutError):
        raise ExportError('Sheets request failed; retry the same sync to update existing rows') from None


def target(metadata):
    if metadata.get('spreadsheetId') != SPREADSHEET_ID:
        raise ExportError('Unexpected spreadsheet')
    matches = [s['properties'] for s in metadata.get('sheets', []) if s['properties'].get('title') == TAB]
    if len(matches) != 1 or type(matches[0].get('sheetId')) is not int:
        raise ExportError(f'Create one tab named exactly {TAB} and retry')
    if matches[0]['sheetId'] != TAB_ID:
        raise ExportError('Destination tab ID changed; refusing to write to another tab')
    if matches[0].get('sheetType', 'GRID') != 'GRID':
        raise ExportError('The destination must be a normal grid tab')
    return matches[0]


def read_owned(access_token, props):
    result = request(access_token, ':getByDataFilter', {
        'dataFilters': [{'gridRange': {'sheetId': props['sheetId'], 'startColumnIndex': 0,
                                      'endColumnIndex': OWNED_COLUMNS}}], 'includeGridData': True})
    if result.get('spreadsheetId') != SPREADSHEET_ID:
        raise ExportError('Unexpected spreadsheet data')
    sheets = result.get('sheets', [])
    if len(sheets) != 1 or sheets[0].get('properties', {}).get('sheetId') != props['sheetId']:
        raise ExportError('Unexpected destination data')
    blocks = sheets[0].get('data', [])
    if len(blocks) > 1 or (blocks and (blocks[0].get('startRow', 0) != 0 or blocks[0].get('startColumn', 0) != 0)):
        raise ExportError('Unexpected destination grid offset')
    cells = blocks[0].get('rowData', []) if blocks else []
    values = []
    for row in cells:
        values.append([c.get('userEnteredValue', {}).get('stringValue',
                       c.get('userEnteredValue', {}).get('numberValue', '')) for c in row.get('values', [])])
    header_index = 1 if values and values[0] and values[0][0] in (SOURCE_TEXT, LEGACY_SOURCE_TEXT) else 0
    header_cells = cells[header_index].get('values', []) if len(cells) > header_index else []
    note = header_cells[0].get('note', '') if header_cells else ''
    # Formulas must also count as content when checking a first-time destination.
    nonempty = any(c.get('userEnteredValue') for row in cells for c in row.get('values', []))
    if nonempty:
        header = values[header_index] if len(values) > header_index else []
        modern = note.startswith(MARKER) and header[:OWNED_COLUMNS] == HEADERS
        legacy = note.startswith(LEGACY_MARKER) and header[:len(LEGACY_HEADERS)] == LEGACY_HEADERS
        if not modern and not legacy:
            raise ExportError('Destination contains unrecognized data; refusing to replace it')
        if legacy:
            # O currently belongs to reviewers; its contents will move to P in
            # the same atomic batch that inserts the new description column.
            values = [row[:len(LEGACY_HEADERS)] for row in values]
    while values and not any(v != '' for v in values[-1]):
        values.pop()
    return values, not nonempty


def cell(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ExportError('Non-finite export value')
        return {'userEnteredValue': {'numberValue': value}}
    return {'userEnteredValue': {'stringValue': str(value)}}


def plan(props, existing, rows, fresh):
    """Update A:O by UUID; migrate the old layout without losing reviewer cells."""
    sheet_id = props['sheetId']
    old_header_row = 1 if existing and existing[0] and existing[0][0] in (SOURCE_TEXT, LEGACY_SOURCE_TEXT) else 0
    upgrading = not fresh and existing[old_header_row] == LEGACY_HEADERS
    add_source = not fresh and old_header_row == 0
    shift = 1 if add_source else 0
    positions = {}
    for index, row in enumerate(existing[old_header_row+1:], old_header_row+1):
        if not any(value != '' for value in row):
            continue
        sid = row[ID_COLUMN] if len(row) > ID_COLUMN else ''
        if not sid or sid in positions:
            raise ExportError('Destination has a missing or duplicate stint ID')
        positions[sid] = index + shift
    requests = []
    next_row = 2 if fresh else max(2, len(existing)+shift)
    if add_source:
        requests.append({'insertDimension': {'range': {'sheetId': sheet_id, 'dimension': 'ROWS',
                          'startIndex': 0, 'endIndex': 1}, 'inheritFromBefore': False}})
    if fresh or add_source or existing[0][0] != SOURCE_TEXT:
        source = cell(SOURCE_TEXT)
        source['textFormatRuns'] = [{'startIndex': len(SOURCE_PREFIX), 'format': {'link': {'uri': SOURCE_URL}}}]
        requests.append({'updateCells': {'start': {'sheetId': sheet_id, 'rowIndex': 0, 'columnIndex': 0},
                         'rows': [{'values': [source]}], 'fields': 'userEnteredValue,textFormatRuns'}})
    if fresh:
        headers = [cell(v) for v in HEADERS]
        headers[0]['note'] = NOTE
        requests.append({'updateCells': {'start': {'sheetId': sheet_id, 'rowIndex': HEADER_ROW, 'columnIndex': 0},
                                         'rows': [{'values': headers}], 'fields': 'userEnteredValue,note'}})
    elif upgrading:
        requests.extend([
            {'insertDimension': {'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
                                           'startIndex': 14, 'endIndex': 15}, 'inheritFromBefore': True}},
            {'updateCells': {'start': {'sheetId': sheet_id, 'rowIndex': HEADER_ROW, 'columnIndex': 14},
                             'rows': [{'values': [cell(HEADERS[-1])]}], 'fields': 'userEnteredValue'}},
            {'updateCells': {'start': {'sheetId': sheet_id, 'rowIndex': HEADER_ROW, 'columnIndex': 0},
                             'rows': [{'values': [{'note': NOTE}]}], 'fields': 'note'}}])
    else:
        requests.append({'updateCells': {'start': {'sheetId': sheet_id, 'rowIndex': HEADER_ROW, 'columnIndex': 0},
                         'rows': [{'values': [{'note': NOTE}]}], 'fields': 'note'}})
    seen = set()
    for row in rows:
        if len(row) != OWNED_COLUMNS or not row[ID_COLUMN] or row[ID_COLUMN] in seen:
            raise ExportError('Invalid or duplicate source stint')
        sid = row[ID_COLUMN]
        seen.add(sid)
        index = positions.get(sid)
        if index is None:
            index, next_row = next_row, next_row+1
        requests.append({'updateCells': {'start': {'sheetId': sheet_id, 'rowIndex': index, 'columnIndex': 0},
                                         'rows': [{'values': [cell(v) for v in row]}], 'fields': 'userEnteredValue'}})
    # Keep excluded rows and reviewers' notes intact, but do not display partial
    # results. A formerly active row becomes visible when it later qualifies.
    for sid, index in positions.items():
        requests.append(row_visibility(sheet_id, index, sid not in seen))
    needed_rows = max(next_row, len(existing)+shift, 3)
    grid = dict(props.get('gridProperties', {}))
    if upgrading:
        grid['columnCount'] = grid.get('columnCount', 0) + 1
    if add_source:
        grid['rowCount'] = grid.get('rowCount', 0) + 1
    needed_columns = OWNED_COLUMNS
    if grid.get('rowCount', 0) < needed_rows or grid.get('columnCount', 0) < needed_columns:
        grow = {'sheetId': sheet_id, 'gridProperties': {
            'rowCount': max(grid.get('rowCount', 0), needed_rows),
            'columnCount': max(grid.get('columnCount', 0), needed_columns)}}
        requests.insert(0, {'updateSheetProperties': {'properties': grow,
                          'fields': 'gridProperties.rowCount,gridProperties.columnCount'}})
    if fresh:
        requests.extend(formatting(sheet_id, needed_rows))
    # Counts are integers. Optional decimal placeholders produce dangling
    # commas (e.g. "3843,") in the spreadsheet's pl_PL locale.
    requests.extend(number_formats(sheet_id, needed_rows))
    if fresh or upgrading:
        requests.extend(description_format(sheet_id, needed_rows))
    if fresh or add_source:
        requests.append(source_format(sheet_id))
    if add_source:
        requests.append({'updateSheetProperties': {'properties': {'sheetId': sheet_id,
                         'gridProperties': {'frozenRowCount': 2}}, 'fields': 'gridProperties.frozenRowCount'}})
    validate_requests(requests, sheet_id, fresh, upgrading, add_source)
    return requests


def formatting(sheet_id, count):
    """Ranking's green header, compact font, neutral rows and first five columns."""
    area = {'sheetId': sheet_id, 'startRowIndex': HEADER_ROW, 'endRowIndex': count,
            'startColumnIndex': 0, 'endColumnIndex': OWNED_COLUMNS}
    green = {'red': 53/255, 'green': 104/255, 'blue': 84/255}
    result = [
        {'repeatCell': {'range': area, 'cell': {'userEnteredFormat': {
            'textFormat': {'fontFamily': 'Helvetica Neue', 'fontSize': 8},
            'horizontalAlignment': 'CENTER', 'verticalAlignment': 'TOP'}}, 'fields': 'userEnteredFormat'}},
        {'repeatCell': {'range': dict(area, endRowIndex=HEADER_ROW+1), 'cell': {'userEnteredFormat': {
            'backgroundColor': green, 'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
            'wrapStrategy': 'WRAP'}}, 'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.wrapStrategy'}},
        {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 0, 'endIndex': 1},
            'properties': {'pixelSize': 170}, 'fields': 'pixelSize'}},
        {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 1, 'endIndex': 5},
            'properties': {'pixelSize': 155}, 'fields': 'pixelSize'}},
        {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 5, 'endIndex': OWNED_COLUMNS},
            'properties': {'pixelSize': 190}, 'fields': 'pixelSize'}},
        {'updateSheetProperties': {'properties': {'sheetId': sheet_id, 'gridProperties': {'frozenRowCount': 2}},
                                   'fields': 'gridProperties.frozenRowCount'}}]
    for index in range(HEADER_ROW+1, count):
        shade = 1 if index % 2 else 246/255
        color = {'red': shade, 'green': 1 if shade == 1 else 248/255,
                 'blue': 1 if shade == 1 else 249/255}
        result.append({'repeatCell': {'range': dict(area, startRowIndex=index, endRowIndex=index+1),
                       'cell': {'userEnteredFormat': {'backgroundColor': color}},
                       'fields': 'userEnteredFormat.backgroundColor'}})
    return result


def number_formats(sheet_id, count):
    result = []
    for start, end, pattern in ((1, 3, '0'), (3, 5, '0.00'), (6, 7, '0')):
        result.append({'repeatCell': {'range': {'sheetId': sheet_id, 'startRowIndex': HEADER_ROW+1,
                       'endRowIndex': count, 'startColumnIndex': start, 'endColumnIndex': end},
                       'cell': {'userEnteredFormat': {'numberFormat': {'type': 'NUMBER', 'pattern': pattern}}},
                       'fields': 'userEnteredFormat.numberFormat'}})
    return result


def description_format(sheet_id, count):
    area = {'sheetId': sheet_id, 'startRowIndex': HEADER_ROW, 'endRowIndex': count,
            'startColumnIndex': 14, 'endColumnIndex': 15}
    return [
        {'repeatCell': {'range': area, 'cell': {'userEnteredFormat': {
            'wrapStrategy': 'WRAP', 'verticalAlignment': 'TOP', 'horizontalAlignment': 'LEFT',
            'textFormat': {'fontFamily': 'Helvetica Neue', 'fontSize': 8}}}, 'fields': 'userEnteredFormat'}},
        {'repeatCell': {'range': dict(area, endRowIndex=HEADER_ROW+1), 'cell': {'userEnteredFormat': {
            'backgroundColor': {'red': 53/255, 'green': 104/255, 'blue': 84/255},
            'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}}}},
            'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.foregroundColor'}},
        {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'COLUMNS',
             'startIndex': 14, 'endIndex': 15}, 'properties': {'pixelSize': 420}, 'fields': 'pixelSize'}}]


def source_format(sheet_id):
    return {'repeatCell': {'range': {'sheetId': sheet_id, 'startRowIndex': 0, 'endRowIndex': 1,
                                    'startColumnIndex': 0, 'endColumnIndex': OWNED_COLUMNS},
        'cell': {'userEnteredFormat': {'backgroundColor': {'red': 1, 'green': 1, 'blue': 1},
                 'textFormat': {'foregroundColor': {'red': 0, 'green': 0, 'blue': 0}, 'bold': False},
                 'horizontalAlignment': 'LEFT', 'wrapStrategy': 'OVERFLOW_CELL'}},
        'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.textFormat.bold,userEnteredFormat.horizontalAlignment,userEnteredFormat.wrapStrategy'}}


def row_visibility(sheet_id, index, hidden):
    return {'updateDimensionProperties': {'range': {'sheetId': sheet_id, 'dimension': 'ROWS',
                         'startIndex': index, 'endIndex': index+1},
                         'properties': {'hiddenByUser': hidden}, 'fields': 'hiddenByUser'}}


def validate_requests(requests, sheet_id, fresh, upgrading=False, add_source=False):
    """Fail closed before making any write request; no source-sheet operations."""
    if sheet_id != TAB_ID:
        raise ExportError('Unexpected destination tab ID')
    for req in requests:
        if len(req) != 1:
            raise ExportError('Invalid Sheets operation')
        kind, body = next(iter(req.items()))
        if kind == 'updateCells':
            start = body['start']
            limit = OWNED_COLUMNS
            column = start.get('columnIndex')
            if start.get('sheetId') != sheet_id or column not in (0, 14) or (column == 14 and not upgrading):
                raise ExportError('Write outside destination')
            if any(column + len(row['values']) > limit for row in body['rows']):
                raise ExportError('Write into human review columns')
        elif kind in ('repeatCell', 'updateDimensionProperties'):
            area = body['range']
            numeric = kind == 'repeatCell' and body['fields'] == 'userEnteredFormat.numberFormat' and (
                area.get('startColumnIndex'), area.get('endColumnIndex')) in ((1, 3), (3, 5), (6, 7))
            description = upgrading and ((kind == 'repeatCell' and area.get('startColumnIndex') == 14 and area.get('endColumnIndex') == 15)
                          or (kind == 'updateDimensionProperties' and area.get('startIndex') == 14 and area.get('endIndex') == 15))
            source = req == source_format(sheet_id)
            visibility = (kind == 'updateDimensionProperties' and body['fields'] == 'hiddenByUser'
                          and area.get('dimension') == 'ROWS' and type(area.get('startIndex')) is int
                          and area['startIndex'] >= HEADER_ROW+1
                          and area.get('endIndex') == area['startIndex']+1
                          and type(body.get('properties', {}).get('hiddenByUser')) is bool)
            if area.get('sheetId') != sheet_id or not (fresh or numeric or description or source or visibility):
                raise ExportError('Formatting outside initial destination setup')
            if kind == 'repeatCell' and area.get('endColumnIndex', OWNED_COLUMNS+1) > OWNED_COLUMNS:
                raise ExportError('Formatting outside managed columns')
            if kind == 'updateDimensionProperties' and not visibility and area.get('endIndex', OWNED_COLUMNS+1) > OWNED_COLUMNS:
                raise ExportError('Dimension update outside managed columns')
        elif kind == 'insertDimension':
            column_insert = upgrading and body['range'] == {'sheetId': sheet_id, 'dimension': 'COLUMNS', 'startIndex': 14, 'endIndex': 15}
            row_insert = add_source and body['range'] == {'sheetId': sheet_id, 'dimension': 'ROWS', 'startIndex': 0, 'endIndex': 1}
            if not (column_insert or row_insert):
                raise ExportError('Column insertion outside schema migration')
        elif kind == 'updateSheetProperties':
            if body['properties'].get('sheetId') != sheet_id or not body['fields'].startswith('gridProperties.'):
                raise ExportError('Property update outside destination')
        else:
            raise ExportError('Unsupported write operation')


def sync(access_token, rows):
    props = target(request(access_token))
    existing, fresh = read_owned(access_token, props)
    changes = plan(props, existing, rows, fresh)
    # Resolve the title again immediately before the atomic batch write.
    if target(request(access_token))['sheetId'] != props['sheetId']:
        raise ExportError('Destination tab changed during export')
    request(access_token, ':batchUpdate', {'requests': changes})
    return {'synced': True, 'spreadsheet_id': SPREADSHEET_ID, 'tab': TAB,
            'sheet_id': props['sheetId'], 'stints': len(rows), 'human_columns_preserved': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['connect', 'status', 'preview', 'sync', 'disconnect'])
    args = parser.parse_args()
    try:
        if args.command == 'preview':
            pass
        elif args.command == 'connect':
            with auth.locked():
                result = auth.authorize(os.environ['YOUTUBE_PROJECT_ID'])
            print(json.dumps(result))
            return 0
        elif args.command == 'disconnect':
            with auth.locked():
                print(json.dumps(auth.disconnect()))
            return 0
        elif args.command == 'status':
            with auth.locked():
                access = auth.token(os.environ['YOUTUBE_PROJECT_ID'])
                props = target(request(access))
                read_owned(access, props)
            print(json.dumps({'connected': True, 'tab': TAB, 'sheet_id': props['sheetId']}))
            return 0
        elif args.command == 'sync':
            from sheets_jobs import run_job
            print(json.dumps(run_job()))
            return 0
        import psycopg
        with psycopg.connect(os.getenv('DATABASE_URL', '')) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            rows = snapshot(conn)
        if args.command == 'preview':
            print(json.dumps({'spreadsheet_id': SPREADSHEET_ID, 'tab': TAB, 'headers': HEADERS,
                              'review_headers': REVIEW_HEADERS, 'rows': rows}, ensure_ascii=False, indent=2))
        return 0
    except (ExportError, auth.AuthorizationRequired, ValueError, KeyError, FileNotFoundError) as exc:
        # Only our own validation messages are safe to show.
        message = str(exc) if isinstance(exc, (ExportError, auth.AuthorizationRequired)) else 'Check Google project, OAuth client file and destination configuration'
        print(message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
