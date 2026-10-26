"""Durable video completion, separate from whether live chat still accepts replies."""
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS stream_endings (
 stream_url text PRIMARY KEY, confirmed_at timestamptz NOT NULL DEFAULT now(),
 actual_ended_at timestamptz, source text NOT NULL, details jsonb NOT NULL
);
"""


class StreamEnded(RuntimeError):
    def __init__(self, evidence):
        super().__init__('Broadcast has finished')
        self.evidence = evidence


class ChatClosed(RuntimeError):
    pass


def ending(conn, stream):
    row = conn.execute('SELECT to_jsonb(e) FROM stream_endings e WHERE stream_url=%s', (stream,)).fetchone()
    return row[0] if row else None


def record_end(conn, stream, evidence):
    from psycopg.types.json import Jsonb
    conn.execute('INSERT INTO stream_endings(stream_url,actual_ended_at,source,details) VALUES (%s,%s,%s,%s) '
                 'ON CONFLICT(stream_url) DO UPDATE SET actual_ended_at='
                 'COALESCE(stream_endings.actual_ended_at,EXCLUDED.actual_ended_at)',
                 (stream, evidence.get('actual_ended_at'), evidence['source'], Jsonb(evidence)))


def finish_state(state, obs, evidence):
    """Close only an active stint, retaining accepted counters and uncertainty."""
    state = json.loads(json.dumps(state))
    obs.update(status='stream_ended', details=dict(obs['details'], stream_ending=evidence))
    if state.get('stream_ended'):
        return state
    presenters = state.setdefault('presenters', {})
    current = presenters.pop('current', None)
    if current:
        stopped = current.get('last_increase_at') or current.get('last_counter_at') or current['detected_at']
        if current.get('last_counter_at') and stopped > current['last_counter_at']:
            stopped = current['last_counter_at']
        current.update(ended_at=stopped, end_reason='broadcast_ended', version_at=obs['observed_at'])
        current['end_evidence'] = {
            'confirmed_at': obs['observed_at'], 'observation_id': obs['id'],
            'estimated_stop_at': stopped, 'estimated_stop_basis': 'last_accepted_increase_or_counter_sample',
            'exact_stop_verified': False, 'presenter_absence_verified': False,
            'broadcast_ending': evidence}
        obs['details']['presenter_session_updates'] = [current]
    state['stream_ended'] = dict(evidence, confirmed_at=obs['observed_at'])
    return state
