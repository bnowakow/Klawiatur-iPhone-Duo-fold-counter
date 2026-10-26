"""Durable same-channel delay; real PostgreSQL, simulated sends, no Google calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json,os,uuid
from pathlib import Path
from unittest.mock import patch
import youtube_bot as bot
import youtube_quota as quota


def main():
    bot.initialize()
    run=uuid.uuid4().hex;project='integration:self-delay:'+run;stream=project
    target={'channel':{'id':'bot:'+run},'chat_id':'chat:'+run}
    bot.remember_state(stream,project,target)
    now=datetime.now(timezone.utc);enabled=now-timedelta(seconds=1)
    sequence=0
    def item(command='!ile',own=True):
        nonlocal sequence
        sequence+=1
        return {'id':run+':'+str(sequence),'type':1,'text':command,
                'author_channel_id':target['channel']['id'] if own else 'other-viewer',
                'published_at':now.isoformat()}
    value=['before delay'];sent=[]
    def fake_send(*args,**kwargs):
        sent.append((args,kwargs));return {'id':str(uuid.uuid4()),'status':'sent'}
    with patch.dict(bot.HANDLERS,{'current_person':lambda *_:value[0]}),patch.object(bot.chat,'send',side_effect=fake_send):
        own=item()
        queued=bot.process_message(project,stream,target,enabled,own,now=now)
        assert queued['status']=='delayed' and queued['delay_seconds']==60,queued
        assert bot.process_message(project,stream,target,enabled,own,now=now)['status']=='duplicate'
        assert not sent
        assert bot.process_delayed_commands(project,stream,now=now+timedelta(seconds=59))==[]
        # A different viewer is not delayed or blocked by a queued own command.
        immediate=bot.process_message(project,stream,target,enabled,item('!pomoc',False),now=now)
        assert immediate['status']=='sent' and len(sent)==1,immediate
        value[0]='freshly computed after delay'
        # Repeated workers race for the same persisted schedule: one send only.
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=list(pool.map(lambda _:bot.process_delayed_commands(project,stream,now=now+timedelta(seconds=60)),range(2)))
        assert sum(len(x) for x in outcomes)==1,outcomes
        assert len(sent)==2 and sent[-1][0][2]==value[0],sent
        assert sent[-1][1]['expires_at']==now+timedelta(seconds=120)
        assert bot.process_delayed_commands(project,stream,now=now+timedelta(seconds=61))==[]
        # A command left pending across a long shutdown expires without sending.
        stale=bot.process_message(project,stream,target,enabled,item('!link'),now=now)
        assert stale['status']=='delayed',stale
        expired=bot.process_delayed_commands(project,stream,now=now+timedelta(seconds=121))
        assert expired[0]['status']=='stale' and len(sent)==2,expired
        # New pause while a command waits is terminal, never deferred to recovery.
        paused=bot.process_message(project,stream,target,enabled,item('!samsung'),now=now)
        assert paused['status']=='delayed',paused
        with quota.connect() as c:
            c.execute('INSERT INTO youtube_account_posting_state(channel_id,not_before) VALUES (%s,%s)',(target['channel']['id'],now+timedelta(minutes=10)))
        blocked=bot.process_delayed_commands(project,stream,now=now+timedelta(seconds=60))
        assert blocked[0]['error_reason']=='account_backoff' and len(sent)==2,blocked
        with quota.connect() as c:
            c.execute('UPDATE youtube_account_posting_state SET not_before=%s WHERE channel_id=%s',(now,target['channel']['id']))
        assert bot.process_delayed_commands(project,stream,now=now+timedelta(seconds=61))==[]
        # Disable or identity changes while waiting must prevent dispatch.
        changed=bot.process_message(project,stream,target,enabled,item('!rekord'),now=now)
        assert changed['status']=='delayed',changed
        with quota.connect() as c:
            c.execute('UPDATE youtube_bot_state SET channel_id=%s WHERE stream_url=%s',('different-channel',stream))
        outcomes=bot.process_delayed_commands(project,stream,now=now+timedelta(seconds=60))
        assert outcomes[0]['error_reason']=='delayed_target_changed' and len(sent)==2,outcomes
    with quota.connect() as c:
        event=c.execute('SELECT status,reply_delay_seconds,reply_not_before,delay_claimed_at,reply_text FROM youtube_bot_command_events WHERE id=%s',(queued['event_id'],)).fetchone()
        assert event[0]=='sent' and event[1]==60 and event[2]==now+timedelta(seconds=60) and event[3] is not None and event[4]==value[0],event
    result={'passed':True,'real_google_requests':0,'run':run,'checks':['same posting channel waits 60 seconds','other viewers immediate','reply generated fresh at due time','original 120-second expiry retained','pending state survives reopening database','duplicate and concurrent claims send once','stale waiting commands expire','new account pause suppresses permanently','posting identity change prevents send','schedule and outcomes retained']}
    root=Path(os.getenv('DATA_DIR','data'))/'test-evidence'/'youtube-command-delay'/run
    root.mkdir(parents=True,exist_ok=True);(root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
