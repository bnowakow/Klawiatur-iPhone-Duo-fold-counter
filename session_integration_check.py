"""Compose integration: timed stints, rates, restart and mid-transaction outage."""
from datetime import datetime, timedelta, timezone
import json
from contextlib import redirect_stdout
import io
import os
import shutil
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

from PIL import Image, ImageDraw
import psycopg

import supervisor


def main():
    cfg = supervisor.config()
    cfg['stream'] = 'integration:sessions:' + str(uuid.uuid4())
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    with tempfile.TemporaryDirectory() as temp:
        cfg['data'] = Path(temp) / 'data'
        fixture = Path(temp) / 'frame.png'
        log = io.StringIO()

        def run(index, value, status='no_clothing_change_detected', fail=False):
            # Fresh fixture bytes for each observation, without external capture.
            image = Image.open('/fixtures/latest/live-frame.jpg').convert('RGB')
            ImageDraw.Draw(image).rectangle((0,0,index+1,5),fill='black')
            image.save(fixture)
            def view(*args, **kwargs):
                return ({}, 'baseline_created' if kwargs.get('reset') else status, 0, None,
                        {'view':'presenter_main', 'tracking':{'changed_since':(base+timedelta(seconds=90)).timestamp()}})
            def unavailable(*args):
                raise psycopg.OperationalError('Simulated failure after analysis')
            with redirect_stdout(log), patch('supervisor.datetime', wraps=datetime) as clock, \
                    patch('supervisor.recognize', return_value=({'value':value,'digits':[]},None)), \
                    patch('supervisor.observe', side_effect=view):
                clock.now.return_value = base + timedelta(seconds=index*30)
                if fail:
                    with patch('supervisor.insert',side_effect=unavailable):
                        return supervisor.cycle(cfg,fixture)
                return supervisor.cycle(cfg,fixture)

        run(0,1000,'baseline_created')
        run(1,1000)
        run(2,1021)
        run(3,1042,'change_pending')
        run(4,1063,'change_pending')
        confirmed = run(5,1084,'possible_handover_review_required')
        assert confirmed['details']['camera_view']['baseline_replaced_for_session']
        current_id = confirmed['details']['presenter_session_id']
        run(6,1105)
        # Model reload happens every cycle. Fail after analysis; DB rolls back.
        failed = run(7,1126,fail=True)
        assert failed['status']=='database_unavailable'
        pending = list((cfg['data']/'outbox').glob('*.json'))
        assert len(pending)==1
        queued = json.loads(pending[0].read_text())
        assert queued['_monitor_state']['presenters']['current']['id']==current_id
        recovered = run(8,1147)
        assert recovered['details']['presenter_session_id']==current_id
        assert not list((cfg['data']/'outbox').glob('*.json'))
        # Replaying an old snapshot twice cannot overwrite later counter evidence.
        with psycopg.connect() as conn:
            supervisor.insert(conn,queued)
            supervisor.insert(conn,queued)
            rows=conn.execute('SELECT detected_at, confirmed_at, folding_started_at, ended_at, '
                              'last_counter, folding_folds_per_hour FROM presenter_session_rates '
                              'WHERE stream_url=%s ORDER BY detected_at',(cfg['stream'],)).fetchall()
            assert len(rows)==2,rows
            old,new=rows
            assert old[3]==base+timedelta(seconds=90),old
            assert old[4]==1021,old
            assert new[0]==base+timedelta(seconds=90),new
            assert new[1]==base+timedelta(seconds=150),new
            assert new[2]==base+timedelta(seconds=120),new
            assert new[4]==1147,new
            assert float(new[5])==2520,new
            state=conn.execute('SELECT state FROM monitor_state WHERE stream_url=%s',(cfg['stream'],)).fetchone()[0]
            assert state['presenters']['current']['last_counter']==1147
            appearances = conn.execute('SELECT status, details FROM presenter_appearances '
                                       'WHERE stream_url=%s', (cfg['stream'],)).fetchall()
            assert len(appearances) == 1, appearances
            assert appearances[0][0] == 'sustained_appearance_review_required', appearances
            assert appearances[0][1]['folding_started_at'] is not None
            recent = conn.execute('SELECT recent_folds_per_minute, measured_seconds, '
                                  'first_counter, sample_count FROM presenter_recent_pace '
                                  'WHERE session_id=%s', (current_id,)).fetchone()
            assert float(recent[0]) == 42, recent
            assert float(recent[1]) == 150, recent
            assert recent[2] == 1042, recent  # Excludes the previous stint.
            assert recent[3] >= 3, recent
        report = {'integration':'passed','stream':cfg['stream'], 'sessions':len(rows),
                  'folding_folds_per_hour':float(new[5]),
                  'checks':['pending start retained','database reload','transaction rollback',
                            'outbox state recovery','idempotent stale replay','SQL rates']}
        archive = Path(os.getenv('SESSION_TEST_OUTPUT_DIR', '/data/test-evidence/presenter-sessions')) / cfg['stream'].split(':')[-1]
        shutil.copytree(temp, archive)
        (archive / 'observations.jsonl').write_text(log.getvalue())
        (archive / 'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(dict(report, evidence_path=str(archive))))


if __name__=='__main__':
    main()
