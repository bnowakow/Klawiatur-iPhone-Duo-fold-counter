"""Preview/apply an audited split at manually reviewed clothing-change evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from PIL import Image

import supervisor
from camera_views import observe
from presenter_sessions import counter_sample, new_session
import clothing_descriptions


def split_sessions(current, before, after, now, audit_id):
    if not after or before['accepted_counter'] is None or after[0]['accepted_counter'] is None:
        raise ValueError('Both reviewed boundary frames need accepted counters')
    first = after[0]
    if datetime.fromisoformat(before['observed_at']) >= datetime.fromisoformat(first['observed_at']):
        raise ValueError('Last old observation must precede first new observation')
    if first['accepted_counter'] < before['accepted_counter']:
        raise ValueError('Counter cannot decrease across a handover')
    previous = json.loads(json.dumps(current))
    previous.update(ended_at=before['observed_at'], end_reason='reviewed_handover_correction',
                    last_counter=before['accepted_counter'], last_counter_at=before['observed_at'],
                    last_increase_at=before['observed_at'], version_at=now)
    evidence = {'confirmed_at': now, 'observation_id': audit_id,
                'estimated_stop_at': before['observed_at'],
                'estimated_stop_basis': 'last_reviewed_old_clothing_observation',
                'last_old_observation_id': before['id'], 'first_new_observation_id': first['id'],
                'boundary_lower': before['observed_at'], 'boundary_upper': first['observed_at'],
                'unattributed_boundary_folds': first['accepted_counter']-before['accepted_counter'],
                'exact_stop_verified': False, 'presenter_absence_verified': False}
    previous['end_evidence'] = evidence
    current = new_session(first, first['observed_at'], 'reviewed_handover_correction')
    current['confirmed_at'] = now
    for obs in after:
        counter_sample(current, obs)
    if current['folding_started_at'] is None:
        raise ValueError('Need a second accepted increase within the reviewed new stint')
    current['version_at'] = now
    return previous, current, evidence


def repair(conn, cfg, old_id, before_id, first_id, apply=False):
    from psycopg.types.json import Jsonb
    if apply:
        supervisor.ensure_schema(conn)
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (cfg['stream'],))
        if any((cfg['data'] / 'outbox').glob('*.json')):
            raise ValueError('Replay the observation outbox before correcting attribution')
    state = conn.execute('SELECT state FROM monitor_state WHERE stream_url=%s', (cfg['stream'],)).fetchone()[0]
    active = state.get('presenters', {}).get('current', {})
    if active.get('id') != str(old_id):
        raise ValueError('The specified old stint must still be current; correction already applied or state changed')
    def source(oid):
        row = conn.execute('SELECT to_jsonb(o) FROM observations o WHERE id=%s AND stream_url=%s',
                           (oid, cfg['stream'])).fetchone()
        if not row or row[0]['details'].get('presenter_session_id') != str(old_id):
            raise ValueError('Boundary observation must belong to the specified stream and old stint')
        return row[0]
    before, first = source(before_id), source(first_id)
    after = [row[0] for row in conn.execute(
        "SELECT to_jsonb(o) FROM observations o WHERE stream_url=%s AND observed_at>=%s "
        "AND details->>'presenter_session_id'=%s ORDER BY observed_at,id",
        (cfg['stream'], first['observed_at'], str(old_id))).fetchall()]
    if not after or after[0]['id'] != first['id']:
        raise ValueError('The first new observation must start the reassignment window')
    now = datetime.now(timezone.utc).isoformat()
    audit_id = str(uuid.uuid4())
    old, new, evidence = split_sessions(active, before, after, now, audit_id)
    summary = {'applied': apply, 'audit_id': audit_id, 'old_session_id': str(old_id),
               'new_session_id': new['id'], 'old_last_counter': old['last_counter'],
               'new_baseline_counter': new['baseline_counter'], 'new_last_counter': new['last_counter'],
               'new_detected_at': new['detected_at'], 'new_folding_started_at': new['folding_started_at'],
               'observations_reassigned': len(after), 'boundary': evidence}
    if not apply:
        return summary
    with Image.open(first['frame_path']) as image:
        clothing, _, _, _, _ = observe(image, {}, datetime.fromisoformat(first['observed_at']).timestamp(),
            first['id'], cfg['roi'], cfg['clothing_threshold'], cfg['confirm'], cfg['interval'], reset=True)
    comparisons = [row[0] for row in conn.execute(
        'SELECT to_jsonb(c) FROM tester_comparisons c WHERE stream_url=%s AND compared_at>=%s',
        (cfg['stream'], first['observed_at'])).fetchall()]
    backup = {'monitor_state': state, 'observations': after, 'comparisons': comparisons}
    state = json.loads(json.dumps(state))
    state.update(clothing=clothing, appearances={})
    state['presenters'] = {'current': new}
    audit = supervisor.observation(cfg)
    audit.update(id=audit_id, observed_at=now, status='presenter_handover_corrected',
                 details={'handover_correction': summary,
                          'presenter_session_updates': [old, new], 'presenter_session_id': new['id']})
    # Queue a description from the reviewed new garment rather than inheriting
    # the previous stint's description. No inference runs in this transaction.
    with Image.open(first['frame_path']) as image:
        job = {'id': first['id'], 'observed_at': first['observed_at'],
               'details': {'camera_view': first['details']['camera_view']}}
        clothing_descriptions.prepare(image, job, state['presenters'])
        if job['details'].get('clothing_description_job'):
            audit['details']['clothing_description_job'] = job['details']['clothing_description_job']
    supervisor.insert(conn, audit)
    conn.execute('INSERT INTO presenter_handover_corrections VALUES (%s,%s,%s,%s,%s,%s)',
                 (audit_id, cfg['stream'], now, old_id, new['id'], Jsonb(dict(summary, before=backup))))
    correction = {'presenter_session_id': new['id'], 'previous_presenter_session_id': str(old_id),
                  'presenter_attribution_correction': audit_id}
    conn.execute('UPDATE observations SET details=details || %s WHERE id=ANY(%s::uuid[])',
                 (Jsonb(correction), [row['id'] for row in after]))
    conn.execute("UPDATE tester_comparisons SET status='superseded_by_handover_correction',"
                 'recent_fpm=NULL,stint_fpm=NULL,recent_gap_fpm=NULL,stint_gap_fpm=NULL,details=details || %s '
                 'WHERE stream_url=%s AND compared_at>=%s',
                 (Jsonb({'attribution_correction': audit_id}), cfg['stream'], first['observed_at']))
    conn.execute('UPDATE monitor_state SET state=%s,updated_at=now() WHERE stream_url=%s',
                 (Jsonb(state), cfg['stream']))
    summary['comparisons_invalidated'] = len(comparisons)
    return summary


def main():
    import psycopg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-session', type=uuid.UUID, required=True)
    parser.add_argument('--last-old-observation', type=uuid.UUID, required=True)
    parser.add_argument('--first-new-observation', type=uuid.UUID, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    cfg = supervisor.config()
    with psycopg.connect(os.getenv('DATABASE_URL', ''), connect_timeout=10) as conn:
        result = repair(conn, cfg, args.old_session, args.last_old_observation,
                        args.first_new_observation, args.apply)
    if args.apply:
        root = cfg['data'] / 'test-evidence/handover-corrections' / result['audit_id']
        root.mkdir(parents=True)
        (root / 'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
