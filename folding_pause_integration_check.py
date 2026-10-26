"""Verify audited freeze closure, persistence and supervisor analysis in isolation."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import supervisor
from presenter_sessions import new_session, counter_sample
from folding_pause import confirm_pause


def main():
    schema = 'folding_pause_test_' + uuid.uuid4().hex
    with psycopg.connect('') as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    try:
        with patch.dict(os.environ, {'PGOPTIONS': '-c search_path=' + schema}):
            cfg = dict(supervisor.config(), stream='integration:'+schema)
            base = datetime.now(timezone.utc) - timedelta(minutes=5)
            observations = []
            for seconds, value in [(0, 74980), (30, 75000), (60, 75000), (90, None)]:
                obs = supervisor.observation(cfg)
                obs.update(observed_at=(base+timedelta(seconds=seconds)).isoformat(), status='analyzed',
                           accepted_counter=value, clothing_status='no_clothing_change_detected')
                observations.append(obs)
            current = new_session(observations[0], observations[0]['observed_at'], 'first_observed')
            current['confirmed_at'] = current['detected_at']
            for obs in observations:
                counter_sample(current, obs)
            current['version_at'] = observations[-1]['observed_at']
            observations[-1]['details']['presenter_session_updates'] = [current]
            state = {'presenters': {'current': current},
                     'appearances': {'attribution_review_required': True},
                     'last_observed_at': observations[-1]['observed_at']}
            with psycopg.connect('') as conn:
                supervisor.ensure_schema(conn)
                for obs in observations:
                    supervisor.insert(conn, obs)
                conn.execute('INSERT INTO monitor_state(stream_url,state) VALUES (%s,%s)', (cfg['stream'],Jsonb(state)))
                conn.commit()
                preview = confirm_pause(conn, cfg['stream'], current['id'], 'User confirms ice test')
                assert not preview['applied']
                assert conn.execute('SELECT ended_at FROM presenter_sessions').fetchone()[0] is None
                result = confirm_pause(conn, cfg['stream'], current['id'], 'User confirms ice test', True)
                conn.commit()
            with psycopg.connect('') as conn:
                saved = conn.execute('SELECT state FROM monitor_state').fetchone()[0]
                assert 'current' not in saved['presenters']
                assert saved['presenters']['activity_pause']['counter'] == 75000
                stop, last, reason = conn.execute('SELECT ended_at,last_counter_at,end_reason FROM presenter_sessions').fetchone()
                assert stop == last == base+timedelta(seconds=30)
                assert reason == 'confirmed_freeze_test_pause'
                audit = conn.execute('SELECT details FROM folding_pause_resolutions').fetchone()[0]
                assert audit['before_state']['appearances']['attribution_review_required']
                assert conn.execute('SELECT count(*) FROM presenter_session_ends').fetchone()[0] == 1
                try:
                    confirm_pause(conn, cfg['stream'], current['id'], 'Duplicate', True)
                    raise AssertionError('Duplicate pause accepted')
                except ValueError:
                    pass
                with tempfile.TemporaryDirectory() as temp:
                    cfg['data'] = Path(temp)
                    obs = supervisor.observation(cfg)
                    updated = supervisor.analyze(cfg, saved, obs,
                        fixture=Path('proof-of-concept/test-data/2026-10-26-freeze-test/frame.jpg'))
                    assert obs['accepted_counter'] is None
                    assert obs['details']['folding_pause']['kind'] == 'freeze_test'
                    assert obs['details']['presenter_appearance_updates'] == []
                    assert 'current' not in updated['presenters']
                    assert not updated['appearances'].get('attribution_review_required')
            print(json.dumps({'passed': True, 'result': result}, indent=2))
    finally:
        with psycopg.connect('') as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    main()
