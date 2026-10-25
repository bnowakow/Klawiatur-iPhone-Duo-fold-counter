"""Durable, per-stream anomaly windows over captured frames (not elapsed time)."""
import json
from contextlib import contextmanager
from pathlib import Path
import shutil
import sqlite3
import uuid

WINDOW = 10


def reasons(obs):
    result = []
    details = obs.get('details', {})
    if obs.get('counter_status') in {'rejected', 'suspect_slow', 'unreadable'}:
        result.append('counter:' + obs['counter_status'])
    clothing = obs.get('clothing_status', '')
    if clothing == 'change_pending' or 'review_required' in clothing:
        result.append('appearance:' + clothing)
    if details.get('presenter_attribution_review_required'):
        result.append('presenter_attribution_review_required')
    if details.get('presenter_appearance_updates'):
        result.append('presenter_appearance_event')
    if obs.get('status') in {'duplicate_frame', 'error', 'database_unavailable'}:
        result.append(obs['status'])
    if details.get('error') or details.get('database_error'):
        result.append('processing_error')
    return result


@contextmanager
def connect(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / 'frame-retention.sqlite3', timeout=30)
    conn.execute('PRAGMA synchronous=FULL')
    conn.execute('''CREATE TABLE IF NOT EXISTS captures (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
        stream TEXT NOT NULL, reasons TEXT NOT NULL, retained INTEGER NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS capture_stream ON captures(stream, seq)')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def register(root, obs):
    """Commit protection before cleanup; retries never advance the window twice."""
    root = Path(root)
    uuid.UUID(obs['id'])
    folder = root / 'frames' / obs['id']
    if folder.is_symlink():
        raise ValueError('Refusing symlink in frame retention')
    if not folder.is_dir() or not any(folder.iterdir()):
        return
    why = reasons(obs)
    obs.setdefault('details', {})['frame_retention'] = {
        'policy': 'anomaly_window', 'before': WINDOW, 'after': WINDOW,
        'anomaly_reasons': why,
        'note': 'Ordinary frames are temporary; later anomalies may preserve them.'}
    with connect(root) as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing = conn.execute('SELECT seq,reasons FROM captures WHERE id=?', (obs['id'],)).fetchone()
        if existing:
            # A post-insert DB failure can promote this same capture to an anomaly.
            if why:
                merged = sorted(set(json.loads(existing[1]) + why))
                conn.execute('UPDATE captures SET reasons=?,retained=1 WHERE id=?',
                             (json.dumps(merged), obs['id']))
                nearby = conn.execute('SELECT id FROM captures WHERE stream=? AND seq<? '
                                      'ORDER BY seq DESC LIMIT ?',
                                      (obs['stream_url'], existing[0], WINDOW)).fetchall()
                nearby += conn.execute('SELECT id FROM captures WHERE stream=? AND seq>? '
                                       'ORDER BY seq LIMIT ?',
                                       (obs['stream_url'], existing[0], WINDOW)).fetchall()
                conn.executemany('UPDATE captures SET retained=1 WHERE id=?', nearby)
            return
        previous = conn.execute('SELECT id,reasons FROM captures WHERE stream=? '
                                'ORDER BY seq DESC LIMIT ?', (obs['stream_url'], WINDOW)).fetchall()
        keep = bool(why) or any(json.loads(row[1]) for row in previous)
        conn.execute('INSERT INTO captures(id,stream,reasons,retained) VALUES (?,?,?,?)',
                     (obs['id'], obs['stream_url'], json.dumps(why), int(keep)))
        if why:
            conn.executemany('UPDATE captures SET retained=1 WHERE id=?',
                             [(row[0],) for row in previous])


def prune(root):
    """Keep anomalies/windows indefinitely plus a rolling ten-frame lookbehind."""
    root = Path(root)
    removed = 0
    with connect(root) as conn:
        conn.execute('BEGIN IMMEDIATE')
        streams = conn.execute('SELECT DISTINCT stream FROM captures').fetchall()
        for (stream,) in streams:
            recent = conn.execute('SELECT seq FROM captures WHERE stream=? ORDER BY seq DESC LIMIT ?',
                                  (stream, WINDOW)).fetchall()
            if len(recent) < WINDOW:
                continue
            rows = conn.execute('SELECT id FROM captures WHERE stream=? AND retained=0 AND seq<?',
                                (stream, recent[-1][0])).fetchall()
            for (identifier,) in rows:
                # Observation IDs are UUIDs; do not follow a manipulated path.
                uuid.UUID(identifier)
                folder = root / 'frames' / identifier
                if folder.is_symlink():
                    raise ValueError('Refusing symlink in frame retention')
                if folder.exists():
                    shutil.rmtree(folder)
                    removed += 1
                conn.execute('DELETE FROM captures WHERE id=?', (identifier,))
    return removed


def migrate(root):
    """Classify existing captures from DB/outbox before enabling their deletion."""
    import psycopg
    import os
    root = Path(root)
    with psycopg.connect(os.getenv('DATABASE_URL', ''), connect_timeout=10) as conn:
        rows = conn.execute('SELECT id,stream_url,observed_at,status,counter_status,clothing_status,details '
                            'FROM observations ORDER BY observed_at,id').fetchall()
    observations = {str(r[0]): dict(zip(('id','stream_url','observed_at','status','counter_status',
                                       'clothing_status','details'), (str(r[0]), *r[1:]))) for r in rows}
    for path in (root / 'outbox').glob('*.json'):
        obs = json.loads(path.read_text())
        observations[obs['id']] = obs
    ordered = sorted(observations.values(), key=lambda o: str(o['observed_at']))
    for obs in ordered:
        register(root, obs)
    return {'classified': len(ordered), 'removed': prune(root)}


if __name__ == '__main__':
    import os
    print(json.dumps(migrate(Path(os.getenv('DATA_DIR', 'data')))))


IMAGE_LIMIT_BYTES = 20_000_000_000
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff',
                  '.svg', '.avif', '.heic', '.heif', '.ico'}


def is_image(path):
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return True
    # Website archives use content hashes without file extensions.
    if path.suffix:
        return False
    with path.open('rb') as source:
        head = source.read(32)
    return (head.startswith((b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a',
                             b'BM', b'II*\x00', b'MM\x00*', b'\x00\x00\x01\x00'))
            or head[:4] == b'RIFF' and head[8:12] == b'WEBP'
            or head[4:8] == b'ftyp' and head[8:12] in {b'avif', b'avis', b'heic', b'heix', b'mif1'})


def enforce_image_limit(root, limit=IMAGE_LIMIT_BYTES):
    """Evict oldest image files only; retain metadata and log every cap breach."""
    import logging
    import os
    import stat
    root = Path(root)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError('Image storage limit must be a positive integer')
    with connect(root) as conn:
        # Serialize quota cleanup with capture protection and other cleaners.
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('CREATE TABLE IF NOT EXISTS image_storage_events ('
                     'id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, '
                     'details TEXT NOT NULL)')
        images = []
        for directory, _, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                path = Path(directory) / name
                try:
                    info = path.lstat()
                    if stat.S_ISREG(info.st_mode) and is_image(path):
                        images.append((info.st_mtime_ns, str(path), info.st_size, info.st_ino))
                except FileNotFoundError:
                    continue
        total = sum(row[2] for row in images)
        report = {'event': 'image_storage_limit_exceeded', 'limit_bytes': limit,
                  'before_bytes': total, 'after_bytes': total, 'deleted_files': [], 'errors': []}
        if total <= limit:
            return report
        logging.error(json.dumps({key: value for key, value in report.items()
                                  if key not in {'deleted_files', 'errors'}}))
        for mtime, name, size, inode in sorted(images):
            if total <= limit:
                break
            path = Path(name)
            try:
                current = path.lstat()
                if not stat.S_ISREG(current.st_mode) or (current.st_ino, current.st_size,
                                                       current.st_mtime_ns) != (inode, size, mtime):
                    continue  # A writer changed this file; reconsider next cycle.
                path.unlink()
                total -= size
                report['deleted_files'].append(str(path.relative_to(root)))
            except FileNotFoundError:
                total -= size
            except OSError as exc:
                report['errors'].append({'path': str(path.relative_to(root)), 'error': str(exc)})
        report['after_bytes'] = total
        report['event'] = ('image_storage_trimmed' if total <= limit else 'image_storage_limit_unmet')
        conn.execute('INSERT INTO image_storage_events(details) VALUES (?)', (json.dumps(report),))
        logging.error(json.dumps(dict(report, deleted_files=len(report['deleted_files']))))
        return report
