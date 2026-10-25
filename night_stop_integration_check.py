"""Verify night-stop persistence and next-stint resumption in real PostgreSQL."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone, time
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw
import psycopg

import supervisor


def main():
    cfg = supervisor.config()
    cfg.update(stream='integration:night-stop:'+str(uuid.uuid4()), interval=30,
               effective_interval=30, night_idle=180, timezone=ZoneInfo('Europe/Warsaw'),
               night_start=time(21,30), night_end=time(6,30))
    base = datetime(2026,10,25,20,58,tzinfo=timezone.utc)
    output=io.StringIO()
    with tempfile.TemporaryDirectory() as temporary:
        cfg['data']=Path(temporary)/'data'
        fixture=Path(temporary)/'frame.png'
        def run(index, value):
            image=Image.open('/fixtures/latest/live-frame.jpg').convert('RGB')
            ImageDraw.Draw(image).rectangle((0,0,index+1,5),fill='black')
            image.save(fixture)
            def camera(*args,**kwargs):
                return ({}, 'baseline_created' if kwargs.get('reset') else
                        'no_clothing_change_detected', 0, None,
                        {'view':'presenter_main','tracking':{}})
            with redirect_stdout(output), patch('supervisor.datetime',wraps=datetime) as clock, \
                 patch('supervisor.recognize',return_value=({'value':value,'digits':[]},None)), \
                 patch('supervisor.observe',side_effect=camera):
                clock.now.return_value=base+timedelta(seconds=index*30)
                return supervisor.cycle(cfg,fixture)
        run(0,100)
        run(1,100)
        run(2,121)
        for index in range(3,9):
            closed=run(index,121)
        ended=closed['details']['presenter_session_updates'][0]
        assert ended['ended_at']==(base+timedelta(seconds=60)).isoformat(),ended
        assert ended['end_evidence']['confirmed_at']==(base+timedelta(seconds=240)).isoformat()
        assert closed['details']['presenter_session_id'] is None
        paused=run(9,121)
        assert paused['details']['presenter_session_id'] is None
        resumed=run(10,142)
        assert resumed['details']['presenter_session_id']!=ended['id']
        assert resumed['details']['camera_view']['baseline_replaced_for_session']
        with psycopg.connect() as conn:
            supervisor.insert(conn,closed)
            supervisor.insert(conn,closed)
            rows=conn.execute('SELECT ended_at,end_reason FROM presenter_sessions '
                              'WHERE stream_url=%s ORDER BY detected_at',(cfg['stream'],)).fetchall()
            evidence=conn.execute('SELECT estimated_stopped_at,confirmed_at,details '
                                  'FROM presenter_session_ends WHERE stream_url=%s',(cfg['stream'],)).fetchall()
            assert len(rows)==2 and rows[0][0] is not None and rows[1][0] is None,rows
            assert len(evidence)==1 and evidence[0][0]==base+timedelta(seconds=60),evidence
            assert evidence[0][1]==base+timedelta(seconds=240),evidence
            assert evidence[0][2]['presenter_absence_verified'] is False
    archive=Path('/data/test-evidence/night-stops')/cfg['stream'].split(':')[-1]
    archive.mkdir(parents=True)
    (archive/'observations.jsonl').write_text(output.getvalue())
    report={'integration':'passed','stream':cfg['stream'],'stints':2,'night_endpoints':1,
            'checks':['estimated stop separate from confirmation','end record persisted',
                      'stationary counter stays closed','activity resumes in a new stint',
                      'idempotent replay','absence remains unverified']}
    (archive/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':
    main()
