"""Archive public ipduotest.pl data independently of OCR/presenter inference."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import signal
import threading
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import uuid

SITE = 'https://ipduotest.pl/'
MAX_BYTES = 20 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS official_data_runs (
 id uuid PRIMARY KEY, started_at timestamptz NOT NULL, completed_at timestamptz NOT NULL,
 site_url text NOT NULL, status text NOT NULL, discovery_error text
);
CREATE TABLE IF NOT EXISTS official_data_blobs (
 sha256 text PRIMARY KEY, content_type text NOT NULL, body bytea NOT NULL
);
CREATE TABLE IF NOT EXISTS official_data_resources (
 run_id uuid NOT NULL REFERENCES official_data_runs(id), resource_key text NOT NULL,
 source_url text NOT NULL, fetched_at timestamptz NOT NULL, status text NOT NULL,
 sha256 text REFERENCES official_data_blobs(sha256), error text,
 metadata jsonb NOT NULL, parsed jsonb,
 PRIMARY KEY (run_id, resource_key)
);
CREATE INDEX IF NOT EXISTS official_data_resources_time
 ON official_data_resources(resource_key, fetched_at DESC);
CREATE OR REPLACE VIEW official_data_latest AS
 SELECT DISTINCT ON (resource_key) * FROM official_data_resources
 ORDER BY resource_key, fetched_at DESC, run_id DESC;
CREATE OR REPLACE VIEW official_data_latest_success AS
 SELECT DISTINCT ON (resource_key) * FROM official_data_resources
 WHERE status='ok' ORDER BY resource_key, fetched_at DESC, run_id DESC;
"""


def now():
    return datetime.now(timezone.utc).isoformat()


class HomeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.images = [], []
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.script = []
        if tag == 'img' and attrs.get('src'):
            self.images.append(attrs['src'])

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append(''.join(self.script))
            self.script = None


def discover(html):
    parser = HomeParser()
    parser.feed(html)
    configs = []
    for script in parser.scripts:
        for match in re.finditer(r'var\s+sheetable_vars\s*=\s*', script):
            value, _ = json.JSONDecoder().raw_decode(script[match.end():])
            configs.append(value)
    if not configs or not configs[-1].get('tables'):
        raise ValueError('No published Sheetable tables discovered; site layout may have changed')
    sources = {}
    for table in configs[-1]['tables']:
        settings = table['settings']
        sheet_id, gid = settings['sheet_id'], str(settings.get('selected_sheet', 0))
        if not re.fullmatch(r'[A-Za-z0-9_-]+', sheet_id) or not gid.isdigit():
            raise ValueError('Unexpected public spreadsheet identifier')
        key = f'sheet:{sheet_id}:{gid}'
        url = f'https://docs.google.com/spreadsheets/u/0/d/{sheet_id}/htmlview/sheet?headers=true&gid={gid}'
        source = sources.setdefault(key, {'resource_key':key, 'source_url':url,
                                         'kind':'sheet', 'metadata':{'tables':[]}})
        source['metadata']['tables'].append(table)
    for src in parser.images:
        url = urljoin(SITE, src)
        if urlparse(url).hostname == urlparse(SITE).hostname:
            key = 'image:' + urlparse(url).path
            sources[key] = {'resource_key':key, 'source_url':url, 'kind':'image', 'metadata':{}}
    return list(sources.values())


class SheetParser(HTMLParser):
    """Keep published data cells, not Google row numbers/column-letter chrome."""
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.rows, self.cells, self.row = [], [], None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table':
            if self.depth:
                self.depth += 1
            elif 'waffle' in attrs.get('class','').split():
                self.depth = 1
        if not self.depth:
            return
        if tag == 'tr':
            self.row = []
        if tag == 'td' and self.row is not None:
            self.cell = {'text':'', 'links':[], 'images':[],
                         'colspan':int(attrs.get('colspan',1)), 'rowspan':int(attrs.get('rowspan',1))}
        if self.cell is not None:
            if tag == 'a' and attrs.get('href'):
                self.cell['links'].append(attrs['href'])
            if tag == 'img' and attrs.get('src'):
                self.cell['images'].append(attrs['src'])
            if tag == 'br':
                self.cell['text'] += '\n'

    def handle_data(self, data):
        if self.cell is not None:
            self.cell['text'] += data

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.cell['text'] = self.cell['text'].strip()
            self.row.append(self.cell)
            self.cell = None
        if tag == 'tr' and self.row is not None:
            if self.row:
                self.cells.append(self.row)
                self.rows.append([cell['text'] for cell in self.row])
            self.row = None
        if tag == 'table' and self.depth:
            self.depth -= 1


def parse_sheet(html):
    parser = SheetParser()
    parser.feed(html)
    if not parser.rows:
        raise ValueError('No published sheet cells found (private, empty or changed HTML)')
    return {'headers':parser.rows[0], 'rows':parser.rows[1:], 'cells':parser.cells,
            'row_count':len(parser.rows)-1,
            'nonempty_row_count':sum(any(cell for cell in row) for row in parser.rows[1:])}


def fetch(source, root, timeout=25):
    resource = dict(source, fetched_at=now(), status='error', sha256=None,
                    error=None, parsed=None, content_type=None)
    try:
        request = Request(source['source_url'], headers={'User-Agent':'FoldingSupervisor/1.0 public-data-archive'})
        with urlopen(request, timeout=timeout) as response:
            resource['metadata'] = dict(source['metadata'], response_url=response.url,
                                        response_headers=dict(response.headers))
            resource['content_type'] = response.headers.get('Content-Type','application/octet-stream')
            body = response.read(MAX_BYTES+1)
        if len(body) > MAX_BYTES:
            raise ValueError('Response exceeds archive size limit')
        digest = hashlib.sha256(body).hexdigest()
        blobs = root / 'blobs'
        blobs.mkdir(parents=True, exist_ok=True)
        if not (blobs / digest).exists():
            temp = blobs / (digest + '.' + str(uuid.uuid4()) + '.tmp')
            temp.write_bytes(body)
            temp.replace(blobs / digest)
        resource['sha256'] = digest
        # Preserve raw responses even when parsing fails.
        if source['kind'] == 'sheet':
            resource['parsed'] = parse_sheet(body.decode('utf-8'))
        resource['status'] = 'ok'
    except Exception as exc:
        resource['error'] = f'{type(exc).__name__}: {exc}'
    return resource


def collect(root):
    root = Path(root)
    run = {'id':str(uuid.uuid4()), 'started_at':now(), 'site_url':SITE,
           'discovery_error':None, 'resources':[]}
    home = fetch({'resource_key':'homepage','source_url':SITE,'kind':'homepage','metadata':{}},root)
    run['resources'].append(home)
    try:
        if home['status'] != 'ok':
            raise ValueError('Homepage unavailable; discovery not possible')
        html = (root / 'blobs' / home['sha256']).read_text()
        sources = discover(html)
        # All published tables, not just the first visible page of each table.
        home['parsed'] = {'sources':sources}
        with ThreadPoolExecutor(max_workers=4) as pool:
            run['resources'].extend(pool.map(lambda src:fetch(src,root), sources))
    except Exception as exc:
        run['discovery_error'] = f'{type(exc).__name__}: {exc}'
    successful = sum(r['status']=='ok' for r in run['resources'])
    run['status'] = ('ok' if not run['discovery_error'] and successful==len(run['resources'])
                     else 'partial' if successful else 'error')
    run['completed_at'] = now()
    pending = root / 'pending'
    pending.mkdir(parents=True,exist_ok=True)
    path = pending / (run['id']+'.json')
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(run,ensure_ascii=False,indent=2)+'\n')
    temp.replace(path)
    return run


def flush(root):
    import psycopg
    from psycopg.types.json import Jsonb
    root = Path(root)
    (root/'runs').mkdir(parents=True,exist_ok=True)
    with psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=10) as conn:
        locked = conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))",
                              ('official-data:'+SITE,)).fetchone()[0]
        if not locked:
            return 0
        conn.execute(SCHEMA)
        paths = sorted((root/'pending').glob('*.json'))
        for path in paths:
            run = json.loads(path.read_text())
            conn.execute('INSERT INTO official_data_runs VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                         tuple(run[key] for key in ('id','started_at','completed_at','site_url','status','discovery_error')))
            for resource in run['resources']:
                digest = resource['sha256']
                if digest:
                    body = (root/'blobs'/digest).read_bytes()
                    if hashlib.sha256(body).hexdigest()!=digest:
                        raise ValueError('Archived response checksum mismatch')
                    conn.execute('INSERT INTO official_data_blobs VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                                 (digest,resource['content_type'],body))
                conn.execute('INSERT INTO official_data_resources VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                             'ON CONFLICT DO NOTHING',
                             (run['id'],resource['resource_key'],resource['source_url'],resource['fetched_at'],
                              resource['status'],digest,resource['error'],Jsonb(resource['metadata']),
                              Jsonb(resource['parsed']) if resource['parsed'] is not None else None))
        # Keep the session lock through file moves, after the commit succeeds.
        conn.commit()
        for path in paths:
            path.replace(root/'runs'/path.name)
    return len(paths)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--replay-only',action='store_true',help='Import saved pending snapshots without fetching')
    args = parser.parse_args()
    interval = float(os.getenv('OFFICIAL_DATA_INTERVAL_SECONDS',300))
    if not math.isfinite(interval) or interval < 60:
        parser.error('OFFICIAL_DATA_INTERVAL_SECONDS must be at least 60')
    root = Path(os.getenv('DATA_DIR','data')) / 'official'
    stopped = threading.Event()
    for signum in (signal.SIGTERM,signal.SIGINT):
        signal.signal(signum,lambda *_:stopped.set())
    while not stopped.is_set():
        run = None if args.replay_only else collect(root)
        result = {'collector':'official_data','run_id':run['id'] if run else None,
                  'status':run['status'] if run else 'replay_only'}
        try:
            result['imported_runs'] = flush(root)
            if run is not None:
                # Independent comparison failure must not discard an archived run.
                try:
                    import psycopg
                    from compare_current_tester import compare
                    with psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=10) as conn:
                        comparison = compare(conn)
                    result['comparison'] = {key:comparison.get(key) for key in
                                            ('id','status','official_fpm','recent_fpm','stint_fpm','stint_gap_fpm')}
                except Exception as exc:
                    result['comparison_error'] = f'{type(exc).__name__}: {exc}'
        except Exception as exc:
            result['database_error'] = f'{type(exc).__name__}: {exc}'
        print(json.dumps(result),flush=True)
        if args.once or args.replay_only:
            return 1 if result.get('database_error') or result['status'] in ('partial','error') else 0
        stopped.wait(interval)


if __name__=='__main__':
    raise SystemExit(main())
