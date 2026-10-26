"""Real PostgreSQL pause/logging checks with simulated Google responses only."""
from datetime import datetime,timedelta,timezone
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
import uuid
from urllib.error import HTTPError
import youtube_bot as bot
import youtube_chat as chat
import youtube_quota as quota
import youtube_rate_limits as limits


def main():
    bot.initialize()
    run=str(uuid.uuid4())
    project='integration:posting-pause:'+run
    channel='integration:posting-channel:'+run
    stream='https://www.youtube.com/watch?v=vaiHWiExDXg&fixture='+run
    target={'channel':{'id':channel},'chat_id':'integration:posting-chat:'+run}
    quota.configure(project)
    now=datetime.now(timezone.utc)
    provider={'reason':'rateLimitExceeded','retry_after_seconds':1800,'provider_message':'Posting too frequently'}
    first=quota.reserve(project,'liveChatMessages.insert',channel)
    quota.finish(first,'failed',403,'rateLimitExceeded',provider=provider)
    with quota.connect() as c:
        initial=limits.paused(c,channel)
        assert initial and initial>=now+timedelta(seconds=1800)
        assert c.execute('SELECT provider_message FROM youtube_api_error_details WHERE request_id=%s',(first,)).fetchone()[0]=='Posting too frequently'
    try:
        quota.reserve(project,'liveChatMessages.insert',channel)
        raise AssertionError('Paused account accepted a reservation')
    except quota.ChatRateLimited as error:
        assert error.reason=='account_backoff'
    item={'id':'incoming:'+run,'type':1,'text':'!pomoc','author_channel_id':'viewer','published_at':now.isoformat()}
    with patch.object(chat,'send',side_effect=AssertionError('Posting must not be attempted')):
        result=bot.process_message(project,stream,target,now-timedelta(seconds=1),item)
        assert result['status']=='throttled' and result['error_reason']=='account_backoff',result
    with patch.dict(os.environ,{'YOUTUBE_CHAT_ENABLED':'true'}),patch.object(chat,'context',side_effect=AssertionError('No discovery calls needed during pause')):
        skipped=chat.send(project,stream,'Fixture text','paused:'+run,channel,target['chat_id'])
        assert skipped['error_reason']=='account_backoff',skipped
    # Expiry permits a new request; a second rejection increases the wait to 20 min.
    with quota.connect() as c:
        c.execute('UPDATE youtube_account_posting_state SET not_before=%s WHERE channel_id=%s',(now-timedelta(seconds=1),channel))
    second=quota.reserve(project,'liveChatMessages.insert',channel)
    quota.finish(second,'failed',403,'rateLimitExceeded')
    with quota.connect() as c:
        state=c.execute('SELECT consecutive_rejections,not_before FROM youtube_account_posting_state WHERE channel_id=%s',(channel,)).fetchone()
        assert state[0]==2 and state[1]>=datetime.now(timezone.utc)+timedelta(seconds=1190)
        assert limits.restore_history(c,channel)==0
        c.execute('DELETE FROM youtube_account_posting_state WHERE channel_id=%s',(channel,))
        restored=limits.restore_history(c,channel)
        assert restored==2 and limits.paused(c,channel)
    result={'passed':True,'run_id':run,'real_google_requests':0,'checks':['shared persistent account pause','provider Retry-After honored','command skipped without send','manual send blocked before discovery','backoff increases after another rejection','history restored without posting','provider diagnostic stored']}
    folder=Path(os.getenv('DATA_DIR','data'))/'test-evidence'/'youtube-posting-pause'/run
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
