"""Persistence, shared pauses and no stale-message replay; no Google calls."""
from datetime import datetime,timedelta,timezone
import io,json,os,uuid
from pathlib import Path
from unittest.mock import MagicMock,patch
from urllib.error import HTTPError
import youtube_chat as chat
import youtube_quota as quota


def main():
    run=uuid.uuid4().hex;project='integration:posting-pause:'+run;channel='test-channel:'+run
    quota.configure(project,10000);chat.initialize()
    target={'channel':{'id':channel},'chat_id':'test-chat:'+run}
    def send(key,**kwargs):
        return chat.send(project,'https://youtu.be/vaiHWiExDXg','Current stats: '+key,run+':'+key,
                         channel,target['chat_id'],source_kind=kwargs.pop('source_kind','command'),**kwargs)
    def failure(retry=None):
        body={'error':{'message':'User posting limit reached','errors':[{'reason':'rateLimitExceeded','domain':'youtube.liveChat'}]}}
        return HTTPError('url',403,'Forbidden',{'Retry-After':retry} if retry else {},io.BytesIO(json.dumps(body).encode()))
    with patch.dict(os.environ,{'YOUTUBE_CHAT_ENABLED':'true'}),patch.object(chat,'context',return_value=target) as context, \
            patch.object(chat.auth,'token',return_value='fake'),patch('youtube_rate_limits.random.uniform',return_value=0):
        with patch.object(quota,'open_request') as api:
            expired=send('expired',expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
            assert expired['error_reason']=='message_expired';api.assert_not_called()
        with patch.object(quota,'open_request',side_effect=failure('1200')) as api:
            original=send('original');assert original['status']=='rejected';assert api.call_count==1
        context.reset_mock()
        with patch.object(quota,'open_request') as api:
            assert send('blocked-notification',source_kind='stint_finished')['error_reason']=='account_backoff'
            api.assert_not_called();context.assert_not_called()
        with quota.connect() as conn:
            record=conn.execute('SELECT provider_message,provider_domain,retry_after_seconds FROM youtube_api_error_details e '
                                'JOIN youtube_api_requests r ON r.id=e.request_id WHERE r.delivery_id=%s',(original['id'],)).fetchone()
            assert record==('User posting limit reached','youtube.liveChat',1200),record
            state=conn.execute('SELECT not_before,consecutive_rejections FROM youtube_account_posting_state WHERE channel_id=%s',(channel,)).fetchone()
            assert state[1]==1
            conn.execute("UPDATE youtube_account_posting_state SET not_before=now()-interval '1 second' WHERE channel_id=%s",(channel,))
        with patch.object(quota,'open_request') as api:
            assert send('original')['status']=='rejected'
            assert send('blocked-notification',source_kind='stint_finished')['status']=='rejected'
            assert send('expired')['status']=='rejected'
            chat.recover();api.assert_not_called()
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"id":"fresh-response"}'
        response.__enter__.return_value.status=200
        with patch.object(quota,'open_request',return_value=response) as api:
            assert send('fresh')['status']=='sent';assert api.call_count==1
        delays=[]
        for index in range(5):
            with quota.connect() as conn:
                conn.execute("UPDATE youtube_account_posting_state SET not_before=now()-interval '1 second' WHERE channel_id=%s",(channel,))
            with patch.object(quota,'open_request',side_effect=failure()):
                assert send('failure-'+str(index))['status']=='rejected'
            with quota.connect() as conn:
                delay=conn.execute('SELECT delay_seconds FROM youtube_posting_backoff_events WHERE channel_id=%s '
                                   'ORDER BY observed_at DESC LIMIT 1',(channel,)).fetchone()[0]
                delays.append(delay)
        assert delays==[600,1200,2400,3600,3600],delays
    result={'passed':True,'project':project,'real_google_requests':0,'checks':[
        'expired text never posts','provider diagnostics retained','Retry-After honored',
        'shared account pause blocks notifications without discovery','restart/recovery never resends old text',
        'only a new fresh key sends after the pause','success resets backoff','10/20/40/60-minute capped backoff'],
        'observed_backoff_seconds':delays}
    root=Path(os.getenv('DATA_DIR','data'))/'test-evidence'/'youtube-rate-limits'/run
    root.mkdir(parents=True,exist_ok=True);(root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
