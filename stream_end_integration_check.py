"""Verify terminal capture state, pending replay, final evidence and post-live chat."""
from contextlib import redirect_stdout
from datetime import datetime,timedelta,timezone
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
import supervisor
import stream_lifecycle as lifecycle
import youtube_bot as bot
from presenter_sessions import new_session,counter_sample


def main():
    schema='stream_end_test_'+uuid.uuid4().hex
    with psycopg.connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ,{'PGOPTIONS':'-c search_path='+schema}), tempfile.TemporaryDirectory() as temp:
        cfg=dict(supervisor.config(),stream='integration:'+schema,data=Path(temp))
        now=datetime.now(timezone.utc)
        state={}
        with psycopg.connect() as conn:
            supervisor.ensure_schema(conn)
            conn.execute(bot.SCHEMA)
            for index,value in enumerate((100,120)):
                obs=supervisor.observation(cfg)
                obs.update(observed_at=(now-timedelta(seconds=90-index*30)).isoformat(),status='analyzed',accepted_counter=value)
                current=state.get('presenters',{}).get('current') or new_session(obs,obs['observed_at'],'test')
                current['confirmed_at']=current.get('confirmed_at') or obs['observed_at']
                counter_sample(current,obs)
                current['version_at']=obs['observed_at']
                state={'presenters':{'current':current},'last_observed_at':obs['observed_at']}
                obs['details']['presenter_session_updates']=[current]
                supervisor.insert(conn,obs)
            conn.execute('INSERT INTO monitor_state(stream_url,state) VALUES (%s,%s)',(cfg['stream'],Jsonb(state)))
            lifecycle.record_end(conn,cfg['stream'],{'source':'youtube.actualEndTime','actual_ended_at':now.isoformat()})
        # A completed capture queued during a database outage must be replayed
        # before finalizing the last trusted total.
        pending=supervisor.observation(cfg)
        pending.update(observed_at=(now-timedelta(seconds=30)).isoformat(),status='analyzed',accepted_counter=130)
        counter_sample(current,pending)
        current['version_at']=pending['observed_at']
        state['last_observed_at']=pending['observed_at']
        pending['details']['presenter_session_updates']=[current]
        pending['_monitor_state']=state
        outbox=cfg['data']/'outbox'
        outbox.mkdir()
        (outbox/(pending['id']+'.json')).write_text(json.dumps(pending))
        with redirect_stdout(io.StringIO()), patch('supervisor.capture',side_effect=AssertionError('Capture after video completion')):
            first=supervisor.cycle(cfg)
            second=supervisor.cycle(cfg)
        assert first['status']==second['status']=='stream_ended'
        assert 'presenter_session_updates' not in second['details']
        assert not list(outbox.glob('*.json'))
        with psycopg.connect() as conn:
            closed=conn.execute('SELECT ended_at,last_counter,end_reason FROM presenter_sessions').fetchone()
            assert closed[1:]==(130,'broadcast_ended'),closed
            assert closed[0].isoformat()==pending['observed_at']
            assert conn.execute('SELECT count(*) FROM presenter_session_ends').fetchone()[0]==1
            saved=conn.execute('SELECT state FROM monitor_state').fetchone()[0]
            assert 'current' not in saved['presenters'] and saved['stream_ended']
            assert conn.execute("SELECT count(*) FROM presenter_video_events WHERE event_kind='folding_finish'").fetchone()[0]==1
        target={'channel':{'id':'fixture-channel'},'chat_id':'fixture-chat',
                'actual_started_at':(now-timedelta(days=1)).isoformat(),'actual_ended_at':now.isoformat()}
        bot.remember_state(cfg['stream'],'fixture-project',target)
        bot.video_finished(cfg['stream'],now.isoformat(),'youtube.actualEndTime')
        with psycopg.connect() as conn:
            assert conn.execute('SELECT connection_status FROM youtube_bot_state').fetchone()[0]!='chat_closed'
        bot.chat_finished(cfg['stream'])
        with psycopg.connect() as conn:
            assert conn.execute('SELECT connection_status FROM youtube_bot_state').fetchone()[0]=='chat_closed'
        print(json.dumps({'integration':'passed','schema':schema,'checks':[
            'outbox flushed before closure','trusted final counter retained','estimated stop separate from video end',
            'no capture or duplicate closure on restart','video review endpoint retained',
            'video end does not close chat','explicit chat closure persisted']}))


if __name__=='__main__':main()
