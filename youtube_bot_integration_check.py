"""Exercise bot cooldowns/statistics against PostgreSQL; no actual Google calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from unittest.mock import patch
import uuid
import youtube_bot as bot
import youtube_chat as chat
import youtube_quota as quota


def main():
    bot.initialize()
    run = str(uuid.uuid4())
    project = 'integration:bot:'+run
    stream = 'integration:bot-stream:'+run
    target = {'chat_id':'integration:chat:'+run,'channel':{'id':'integration:channel:'+run}}
    now = datetime.now(timezone.utc)
    enabled = now-timedelta(seconds=10)
    calls = []
    def fake_send(*args, **kwargs):
        calls.append(args[3])
        return {'id':str(uuid.uuid4()),'status':'sent','response_id':'fake-message'}
    def item(key, text='!ile', published=None):
        return {'id':run+':'+key,'type':1,'text':text,'author_channel_id':'viewer:'+key,
                'published_at':(published or datetime.now(timezone.utc)).isoformat()}
    with patch.dict(bot.HANDLERS, {'current_person':lambda stream, config:'Fixture reply. '+config[3]}), patch.object(bot.chat,'send',side_effect=fake_send):
        first = item('first')
        assert bot.process_message(project,stream,target,enabled,first)['status']=='sent'
        assert bot.process_message(project,stream,target,enabled,first)['status']=='duplicate'
        assert bot.process_message(project,stream,target,enabled,item('second'))['status']=='throttled'
        assert bot.process_message(project,stream,target,enabled,item('unknown','!unknown')) is None
        assert bot.process_message(project,stream,target,enabled,item('old',published=enabled-timedelta(seconds=1))) is None
        assert bot.process_message(project,stream,target,enabled-timedelta(days=1),item('stale',published=now-timedelta(minutes=3)))['status']=='stale'
        # The exact stored boundary is permitted, while any earlier command is blocked.
        with quota.connect() as conn:
            boundary=conn.execute('SELECT available_at FROM youtube_bot_cooldowns WHERE chat_id=%s AND command=%s',(target['chat_id'],'!ile')).fetchone()[0]
        assert bot.process_message(project,stream,target,enabled,item('boundary',published=boundary),now=boundary)['status']=='sent'
        assert len(calls)==2
    concurrent_target = {'chat_id':target['chat_id']+':concurrent','channel':target['channel']}
    concurrent_calls = []
    def concurrent_send(*args,**kwargs):
        concurrent_calls.append(args[3])
        return {'id':str(uuid.uuid4()),'status':'unknown'}
    with patch.dict(bot.HANDLERS, {'current_person':lambda stream, config:'Fixture reply'}), patch.object(bot.chat,'send',side_effect=concurrent_send):
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda n:bot.process_message(project,stream,concurrent_target,enabled,item('parallel'+str(n))),range(4)))
        assert len(concurrent_calls)==1, outcomes
        assert sum(result['status']=='throttled' for result in outcomes)==3
        assert bot.process_message(project,stream,concurrent_target,enabled,item('after-unknown'))['status']=='throttled'
    unavailable_target={'chat_id':target['chat_id']+':unavailable','channel':target['channel']}
    with patch.dict(bot.HANDLERS, {'current_person':lambda *_: (_ for _ in ()).throw(ValueError('stale data'))}), patch.object(bot.chat,'send') as send:
        assert bot.process_message(project,stream,unavailable_target,enabled,item('unavailable'))['status']=='unavailable'
        send.assert_not_called()
    # Persist/resume the cursor rather than revisiting history after restarts.
    assert bot.remember_state(stream,project,target)[1] is None
    bot.heartbeat(stream,'streaming',page_token='fixture-cursor',batch=True)
    previous_enabled,cursor = bot.remember_state(stream,project,target)
    assert cursor=='fixture-cursor'
    assert bot.remember_state(stream,project,target)[0]==previous_enabled
    rotated={'chat_id':target['chat_id']+':rotated','channel':target['channel']}
    assert bot.remember_state(stream,project,rotated)[1] is None
    with quota.connect() as conn:
        usage=conn.execute('SELECT received,answered,throttled,stale FROM youtube_bot_command_usage WHERE stream_url=%s AND chat_id=%s',(stream,target['chat_id'])).fetchone()
        assert usage==(4,2,1,1), usage
        daily=conn.execute('SELECT sum(received),sum(answered) FROM youtube_bot_command_daily_usage WHERE stream_url=%s',(stream,)).fetchone()
        assert daily==(10,2), daily
    stats = bot.statistics(stream)
    assert all(row['stream_url']==stream for row in stats['usage'])
    assert sum(row['received'] for row in stats['usage'])==10
    # Help works independently of folding data and has its own chat-wide cap.
    with patch.object(bot.chat,'send',side_effect=fake_send):
        help_result=bot.process_message(project,stream,target,enabled,item('help','!pomoc'))
        assert help_result['status']=='sent',help_result
        assert bot.process_message(project,stream,target,enabled,item('help-throttled','!pomoc'))['status']=='throttled'
    with quota.connect() as conn:
        usage=conn.execute("SELECT received,answered,throttled FROM youtube_bot_command_usage WHERE stream_url=%s AND command='!pomoc'",(stream,)).fetchone()
        assert usage==(2,1,1),usage
        policy=conn.execute("SELECT cooldown_seconds,description FROM youtube_bot_commands WHERE command='!pomoc'").fetchone()
        assert policy[0]==600 and policy[1]
        cached=conn.execute("SELECT source_url,facts,cached_at FROM youtube_bot_command_cache WHERE command='!samsung'").fetchone()
        assert cached[0]==bot.SAMSUNG_SOURCE_URL and 'fbclid' not in cached[0]
        assert cached[1]['model']=='Samsung Galaxy Z Flip3' and cached[1]['final_folds']==418500
        assert cached[1]['still_working_at_end'] and not cached[1]['could_close_fully_at_end']
    bot.initialize()
    with quota.connect() as conn:
        assert conn.execute("SELECT cached_at FROM youtube_bot_command_cache WHERE command='!samsung'").fetchone()[0]==cached[2]
    with patch.object(bot.chat,'send',side_effect=fake_send):
        assert bot.process_message(project,stream,target,enabled,item('samsung','!samsung'))['status']=='sent'
        assert bot.process_message(project,stream,target,enabled,item('samsung-throttled','!samsung'))['status']=='throttled'
    with quota.connect() as conn:
        assert conn.execute("SELECT received,answered,throttled FROM youtube_bot_command_usage WHERE stream_url=%s AND command='!samsung'",(stream,)).fetchone()==(2,1,1)
    assert '!samsung' in bot.build_help_reply(stream,bot.command_config('!pomoc'))
    result={'passed':True,'run_id':run,'real_google_requests':0,'checks':['command statistics','deduplication','global cooldown across users','exact cooldown boundary','concurrent requests','uncertain sends retain cooldown','stale history ignored','unavailable measurements','persistent cursor and chat rotation']}
    folder=Path(os.getenv('DATA_DIR','data'))/'test-evidence'/'youtube-bot'/run
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
