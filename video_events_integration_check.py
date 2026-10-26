"""Isolated bookmarks, finish evidence, replay and reviewer-field persistence."""
from datetime import datetime, timedelta, timezone
import json
import os
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql

import supervisor
import presenter_video_events as video
from presenter_sessions import new_session, counter_sample


def main():
    schema = 'video_events_test_' + uuid.uuid4().hex
    with psycopg.connect(os.getenv('DATABASE_URL', '')) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ, {'PGOPTIONS': '-c search_path=' + schema}):
        with psycopg.connect(os.getenv('DATABASE_URL', '')) as conn:
            supervisor.ensure_schema(conn)
            base = datetime(2026, 10, 26, 6, tzinfo=timezone.utc)
            cfg = dict(supervisor.config(), stream='integration:' + schema)
            current = None
            saved = []
            for seconds, count in [(0, 100), (30, 120), (60, 140)]:
                obs = supervisor.observation(cfg)
                obs.update(observed_at=(base + timedelta(seconds=seconds)).isoformat(),
                           frame_path=f'/fixtures/frame-{seconds}.jpg', status='analyzed',
                           accepted_counter=count)
                obs['details']['capture'] = {'video_timing': {
                    'video_id': 'vaiHWiExDXg', 'broadcast_started_at': (base-timedelta(hours=1)).isoformat()}}
                if current is None:
                    current = new_session(obs, obs['observed_at'], 'first_observed')
                    current['confirmed_at'] = obs['observed_at']
                counter_sample(current, obs)
                current['version_at'] = obs['observed_at']
                obs['details']['presenter_session_updates'] = [json.loads(json.dumps(current))]
                supervisor.insert(conn, obs)
                saved.append(obs)
            current.update(ended_at=saved[-1]['observed_at'], end_reason='sustained_clothing_change',
                           version_at=(base+timedelta(seconds=90)).isoformat())
            end = supervisor.observation(cfg)
            end.update(observed_at=current['version_at'], status='analyzed')
            end['details']['presenter_session_updates'] = [current]
            supervisor.insert(conn, end)
            rows = conn.execute('SELECT event_kind,video_offset_seconds,source_observation_id,details '
                                'FROM presenter_video_events ORDER BY event_at').fetchall()
            assert [r[0] for r in rows] == ['appearance_start', 'folding_start', 'folding_finish'], rows
            assert [r[1] for r in rows] == [3600, 3630, 3660]
            assert str(rows[1][2]) == saved[1]['id']
            assert rows[2][3]['previous_sample']['accepted_counter'] == 120
            assert rows[2][3]['confirmed_at'] == end['observed_at']
            assert rows[2][3]['confirmation_observation_id'] == end['id']
            assert not rows[1][3]['training_label_verified']
            conn.execute("UPDATE presenter_video_events SET review_status='corrected',reviewer='integration',"
                         "reviewed_at=now(),reviewed_video_offset_seconds=3625 WHERE event_kind='folding_start'")
            supervisor.insert(conn, end)  # replay is idempotent and retains review labels
            assert conn.execute('SELECT count(*) FROM presenter_video_events').fetchone()[0] == 3
            assert conn.execute("SELECT reviewed_video_offset_seconds FROM presenter_video_events "
                                "WHERE event_kind='folding_start'").fetchone()[0] == 3625
            # Legacy frames can acquire an explicitly estimated anchor later.
            conn.execute("UPDATE observations SET details=details-'capture' WHERE id=%s", (saved[0]['id'],))
            conn.execute("UPDATE presenter_video_events SET video_offset_seconds=NULL WHERE event_kind='appearance_start'")
            video.save_session(conn, current, end)
            assert conn.execute("SELECT video_offset_seconds FROM presenter_video_events "
                                "WHERE event_kind='appearance_start'").fetchone()[0] == 3600
        print(json.dumps({'integration': 'passed', 'schema': schema,
                          'checks': ['appearance/folding/finish offsets', 'review lead-in and source frames',
                                     'unknown latency explicit', 'replay idempotency', 'review labels preserved',
                                     'legacy timeline anchor backfill']}))


if __name__ == '__main__':
    main()
