"""Test final-stint notifications against PostgreSQL with simulated sends only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
from unittest.mock import patch
import uuid
import youtube_bot as bot
import youtube_stint_notifications as notifications
import youtube_quota as quota


def main():
    bot.initialize()
    run=str(uuid.uuid4())
    stream='integration:stint-notifications:'+run
    notifications.initialize(stream)
    now=datetime.now(timezone.utc)
    with quota.connect() as conn:
        conn.execute('UPDATE youtube_stint_notification_settings SET enabled_at=%s WHERE stream_url=%s',
                     (now-timedelta(hours=3),stream))
    def session(reason='sustained_clothing_change',ended=None,baseline=100,active=False):
        sid=str(uuid.uuid4())
        ended=ended or now-timedelta(seconds=30)
        detected=ended-timedelta(minutes=10)
        with quota.connect() as conn:
            conn.execute('INSERT INTO presenter_sessions '
                         '(id,stream_url,detected_at,confirmed_at,detection_reason,first_observation_id, '
                         'folding_started_at,folding_start_counter,baseline_counter,last_counter,last_counter_at,ended_at,end_reason,version_at) '
                         'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                         (sid,stream,detected,detected+timedelta(seconds=30),'integration',str(uuid.uuid4()),
                          detected+timedelta(seconds=30),baseline,baseline,400,ended-timedelta(seconds=10),
                          None if active else ended,None if active else reason,now))
        return sid
    normal=session()
    night=session('night_pause_counter_stopped_review_required',now-timedelta(minutes=3))
    historical=session(ended=now-timedelta(hours=4))
    manual=session('manual_baseline_reset')
    active=session(active=True)
    expired=session(ended=now-timedelta(hours=2))
    unavailable=session(baseline=None)
    sent=[]
    target={'channel':{'id':'fixture-channel'},'chat_id':'fixture-chat'}
    def fake_send(*args,**kwargs):
        sent.append((args,kwargs))
        return {'id':str(uuid.uuid4()),'status':'sent'}
    with patch.object(notifications.chat,'context',return_value=target),patch.object(notifications.chat,'send',side_effect=fake_send):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:notifications.process_finished('fixture-project',stream),range(2)))
        assert len(sent)==2,len(sent)
        assert all(call[1]['source_kind']=='stint_finished' for call in sent)
        assert all(call[0][2].endswith(bot.DISCLAIMER) for call in sent)
        assert notifications.process_finished('fixture-project',stream)==[]
    with quota.connect() as conn:
        rows=conn.execute('SELECT session_id,status FROM youtube_stint_notifications WHERE stream_url=%s',(stream,)).fetchall()
        states={str(sid):status for sid,status in rows}
        assert states=={normal:'sent',night:'sent',expired:'expired',unavailable:'unavailable'},states
        # Simulate restart after a notification claim with no certain delivery.
        conn.execute("UPDATE youtube_stint_notifications SET status='processing',created_at=now()-interval '10 minutes' WHERE session_id=%s",(normal,))
    notifications.recover()
    with quota.connect() as conn:
        assert conn.execute('SELECT status FROM youtube_stint_notifications WHERE session_id=%s',(normal,)).fetchone()[0]=='unknown'
        conn.execute('UPDATE youtube_stint_notification_settings SET enabled=false WHERE stream_url=%s',(stream,))
    session()
    assert notifications.process_finished('fixture-project',stream)==[]
    result={'passed':True,'run_id':run,'real_google_requests':0,'checks':['completed stint final data','handover and night closure','historical and active stints ignored','manual reset ignored','expired and unavailable data recorded','concurrent and repeated claims deduplicated','unknown outcomes never resent','persisted enable switch']}
    folder=Path(os.getenv('DATA_DIR','data'))/'test-evidence'/'youtube-stint-notifications'/run
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
