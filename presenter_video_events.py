"""Reviewable video bookmarks for inferred stint boundaries, never training labels."""
import argparse
from datetime import datetime, timezone
import json
import math
import os
import re

SCHEMA = """
CREATE TABLE IF NOT EXISTS presenter_video_events (
 session_id uuid NOT NULL REFERENCES presenter_sessions(id),
 event_kind text NOT NULL CHECK (event_kind IN ('appearance_start','folding_start','folding_finish')),
 stream_url text NOT NULL, event_at timestamptz NOT NULL,
 source_observation_id uuid REFERENCES observations(id),
 video_id text, video_offset_seconds double precision, review_url text,
 timing_quality text NOT NULL, details jsonb NOT NULL, version_at timestamptz NOT NULL,
 review_status text NOT NULL DEFAULT 'unreviewed'
   CHECK (review_status IN ('unreviewed','confirmed','corrected','rejected')),
 reviewed_at timestamptz, reviewer text, review_notes text,
 reviewed_video_offset_seconds double precision CHECK (reviewed_video_offset_seconds >= 0),
 PRIMARY KEY (session_id,event_kind)
);
CREATE INDEX IF NOT EXISTS presenter_video_events_stream_time
 ON presenter_video_events(stream_url,event_at DESC);
"""


def capture_metadata(info):
    """yt-dlp's release_timestamp is YouTube liveBroadcastDetails.startTimestamp.

    Upload dates and scheduled dates are deliberately not timeline anchors.
    Signed media URLs and the rest of the extractor response are not persisted.
    """
    result = {'video_id': info.get('id'), 'broadcast_started_at': None}
    value = info.get('release_timestamp')
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0):
        try:
            result['broadcast_started_at'] = datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    return result


def bookmark(event_at, timing):
    result = {'video_id': timing.get('video_id'), 'video_offset_seconds': None,
              'review_url': None, 'timing_quality': 'unavailable'}
    start = timing.get('broadcast_started_at')
    if not start:
        return result
    offset = (datetime.fromisoformat(event_at)-datetime.fromisoformat(start)).total_seconds()
    if offset < 0:
        return result
    result.update(video_offset_seconds=round(offset, 3), timing_quality='estimated_from_capture_clock')
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', result['video_id'] or ''):
        result['review_url'] = f"https://www.youtube.com/watch?v={result['video_id']}&t={max(0, math.floor(offset)-30)}s"
    return result


def save(conn, obs):
    for session in obs.get('details', {}).get('presenter_session_updates', []):
        save_session(conn, session, obs)


def save_session(conn, session, confirmation):
    from psycopg.types.json import Jsonb
    events = [('appearance_start', session['detected_at'], session['first_observation_id'])]
    if session.get('folding_started_at'):
        events.append(('folding_start', session['folding_started_at'], session['folding_start_observation_id']))
    if session.get('ended_at'):
        events.append(('folding_finish', session['ended_at'], None))
    for kind, stamp, source_id in events:
        if source_id:
            row = conn.execute('SELECT to_jsonb(o) FROM observations o WHERE id=%s AND stream_url=%s',
                               (source_id, session['stream_url'])).fetchone()
        else:
            row = conn.execute('SELECT to_jsonb(o) FROM observations o WHERE stream_url=%s '
                               'AND observed_at>=%s AND frame_path IS NOT NULL ORDER BY observed_at,id LIMIT 1',
                               (session['stream_url'], stamp)).fetchone()
        source = row[0] if row else {}
        source_id = source.get('id')
        capture = source.get('details', {}).get('capture', {})
        timing = capture.get('video_timing', {})
        anchor_id = source_id
        if not timing.get('broadcast_started_at'):
            # Backfill legacy boundaries only from a captured timeline anchor
            # of this same stream. Never substitute the upload/scheduled date.
            anchor = conn.execute("SELECT id,details->'capture'->'video_timing' FROM observations "
                                  "WHERE stream_url=%s AND details->'capture'->'video_timing'->>'broadcast_started_at' IS NOT NULL "
                                  'ORDER BY observed_at DESC LIMIT 1', (session['stream_url'],)).fetchone()
            if anchor:
                anchor_id, timing = str(anchor[0]), anchor[1]
        mark = bookmark(stamp, timing)
        previous = conn.execute('SELECT id,observed_at,frame_path,accepted_counter FROM observations '
                                'WHERE stream_url=%s AND observed_at<%s AND frame_path IS NOT NULL '
                                'ORDER BY observed_at DESC,id LIMIT 1', (session['stream_url'], stamp)).fetchone()
        before = ({'observation_id': str(previous[0]), 'observed_at': previous[1].isoformat(),
                   'frame_path': previous[2], 'accepted_counter': previous[3]} if previous else None)
        details = {'prediction': True, 'training_label_verified': False,
                   'event_basis': session.get('end_reason') if kind == 'folding_finish' else session['detection_reason'],
                   'confirmed_at': confirmation['observed_at'],
                   'confirmation_observation_id': confirmation['id'],
                   'source_frame_path': source.get('frame_path'),
                   'source_capture_at': source.get('observed_at'),
                   'source_counter': source.get('accepted_counter'),
                   'previous_sample': before, 'end_evidence': session.get('end_evidence'),
                   'last_attributed_counter_at': session.get('last_counter_at') if kind == 'folding_finish' else None,
                   'folding_baseline_at': session.get('baseline_counter_at') if kind == 'folding_start' else None,
                   'broadcast_started_at': timing.get('broadcast_started_at'),
                   'timeline_anchor_observation_id': anchor_id,
                   'review_preroll_seconds': 30,
                   'timing_note': 'Capture-clock estimate; live latency is unknown. Exact physical start/finish and replay availability are unverified.'}
        conn.execute('INSERT INTO presenter_video_events '
                     '(session_id,event_kind,stream_url,event_at,source_observation_id,video_id,video_offset_seconds,'
                     'review_url,timing_quality,details,version_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                     'ON CONFLICT(session_id,event_kind) DO UPDATE SET '
                     'event_at=EXCLUDED.event_at,source_observation_id=EXCLUDED.source_observation_id,'
                     'video_id=EXCLUDED.video_id,video_offset_seconds=EXCLUDED.video_offset_seconds,'
                     'review_url=EXCLUDED.review_url,timing_quality=EXCLUDED.timing_quality,'
                     'details=EXCLUDED.details,version_at=EXCLUDED.version_at '
                     'WHERE presenter_video_events.version_at <= EXCLUDED.version_at AND '
                     '(presenter_video_events.event_at IS DISTINCT FROM EXCLUDED.event_at OR '
                     'presenter_video_events.source_observation_id IS DISTINCT FROM EXCLUDED.source_observation_id OR '
                     'presenter_video_events.video_offset_seconds IS DISTINCT FROM EXCLUDED.video_offset_seconds)',
                     (session['id'], kind, session['stream_url'], stamp, source_id, mark['video_id'],
                      mark['video_offset_seconds'], mark['review_url'], mark['timing_quality'], Jsonb(details), session['version_at']))


def main():
    import psycopg
    from psycopg.rows import dict_row
    import supervisor
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backfill', action='store_true', help='Create bookmarks for existing stints without changing their counts')
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    cfg = supervisor.config()
    with psycopg.connect(os.getenv('DATABASE_URL', ''), connect_timeout=10) as conn:
        supervisor.ensure_schema(conn)
        if args.backfill:
            rows = conn.execute('SELECT to_jsonb(s) FROM presenter_sessions s WHERE stream_url=%s', (cfg['stream'],)).fetchall()
            for row in rows:
                session = row[0]
                end = conn.execute('SELECT details FROM presenter_session_ends WHERE session_id=%s', (session['id'],)).fetchone()
                if end:
                    session['end_evidence'] = end[0]
                save_session(conn, session, {'id': session['first_observation_id'], 'observed_at': session['confirmed_at']})
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT * FROM presenter_video_events WHERE stream_url=%s ORDER BY event_at DESC, event_kind LIMIT %s',
                        (cfg['stream'], args.limit))
            print(json.dumps(cur.fetchall(), default=str, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
