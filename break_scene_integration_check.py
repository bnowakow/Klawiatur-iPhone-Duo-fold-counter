"""Replay real transition/break pixels through the supervisor and PostgreSQL."""
from contextlib import redirect_stdout
from datetime import datetime, time, timedelta, timezone
import io
import json
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch
import uuid
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
import psycopg

import supervisor
import refine_break_endpoint


def main():
    cfg=supervisor.config()
    cfg.update(stream='integration:break-scene:'+str(uuid.uuid4()),interval=30,
               effective_interval=30,night_idle=180,timezone=ZoneInfo('Europe/Warsaw'),
               night_start=time(21,30),night_end=time(6,30),start=time(0),end=time(0))
    root=Path('/fixtures/test-data/2026-10-25-night-break')
    base=datetime(2026,10,25,20,59,tzinfo=timezone.utc)
    log=io.StringIO()
    statuses=[]
    with tempfile.TemporaryDirectory() as temp:
        cfg['data']=Path(temp)/'data'
        fixture=Path(temp)/'frame.png'
        def run(index):
            name=('ac3dd6ef-72e8-4615-af22-15c2f4b1bac7.jpg' if index<2 else
                  '4771be03-dab0-47fc-9062-d4fa2e3d3cc4.jpg' if index==2 else
                  '01ab91c9-43ef-4f55-8d1d-244bbea6990c.jpg' if index%2 else
                  '81fb9ca9-4b8c-4bc5-92f6-4ac68f315b1e.jpg')
            image=Image.open(root/name).convert('RGB')
            # Vary a corner to simulate distinct frames; all OCR/scene pixels
            # remain exactly those of the real saved captures.
            ImageDraw.Draw(image).rectangle((0,0,index+1,5),fill='black')
            image.save(fixture)
            with redirect_stdout(log), patch('supervisor.datetime',wraps=datetime) as clock:
                clock.now.return_value=base+timedelta(seconds=index*30+(270 if index>=3 else 0))
                result=supervisor.cycle(cfg,fixture)
            statuses.append(result.get('counter_status'))
            return result
        for index in range(12):
            closed=run(index)
        assert 'accepted_break_boundary' in statuses,statuses
        assert closed['clothing_status']=='night_break_detected',closed
        assert closed['accepted_counter']==63333,closed
        assert closed['details']['presenter_session_id'] is None,closed
        still_paused=run(12)
        assert still_paused['details']['presenter_session_id'] is None
        with psycopg.connect() as conn:
            supervisor.insert(conn,closed)
            supervisor.insert(conn,closed)
            rows=conn.execute('SELECT ended_at,end_reason,last_counter FROM presenter_sessions '
                              'WHERE stream_url=%s',(cfg['stream'],)).fetchall()
            evidence=conn.execute('SELECT estimated_stopped_at,confirmed_at,details '
                                  'FROM presenter_session_ends WHERE stream_url=%s',(cfg['stream'],)).fetchall()
            assert len(rows)==1 and rows[0][0]==base+timedelta(seconds=60),rows
            assert rows[0][2]==63330,rows  # The transition's final 3 folds remain unattributed.
            assert len(evidence)==1 and evidence[0][2]['break_scene_verified'],evidence
            assert evidence[0][2]['counter']==63333,evidence
            assert evidence[0][1]==base+timedelta(seconds=600),evidence
            session_id=conn.execute('SELECT id FROM presenter_sessions WHERE stream_url=%s',
                                    (cfg['stream'],)).fetchone()[0]
            # Reproduce the legacy late-stop estimate, then exercise the exact
            # auditable refinement tool before using it on live history.
            conn.execute('UPDATE presenter_sessions SET ended_at=%s WHERE id=%s',
                         (base+timedelta(seconds=420),session_id))
            conn.execute('UPDATE presenter_session_ends SET estimated_stopped_at=%s WHERE session_id=%s',
                         (base+timedelta(seconds=420),session_id))
        with patch('sys.argv',['refine_break_endpoint.py','--session',str(session_id),'--apply']), \
             patch('supervisor.config',return_value=cfg), redirect_stdout(log):
            refine_break_endpoint.main()
        with psycopg.connect() as conn:
            refined=conn.execute('SELECT estimated_stopped_at,confirmed_at FROM presenter_session_ends '
                                 'WHERE session_id=%s',(session_id,)).fetchone()
            assert refined[0]==base+timedelta(seconds=60) and refined[1]==base+timedelta(seconds=600),refined
            assert conn.execute("SELECT count(*) FROM observations WHERE stream_url=%s AND "
                                "status='night_break_endpoint_refined'",(cfg['stream'],)).fetchone()[0]==1
        archive=Path('/data/test-evidence/break-scenes')/cfg['stream'].split(':')[-1]
        shutil.copytree(temp,archive)
        (archive/'observations.jsonl').write_text(log.getvalue())
        report={'integration':'passed','stream':cfg['stream'],'final_counter':63333,
                'attributed_session_counter':63330,'stints':len(rows),'break_endpoints':len(evidence),
                'checks':['real pixels, no mocked OCR or layout detector','thin LED decoding',
                          'repeated boundary confirmation','night stint closed',
                          'final transition folds unassigned','idempotent endpoint replay',
                          'audited legacy endpoint refinement']}
        (archive/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))


if __name__=='__main__':
    main()
