"""One explicitly authorized fresh help POST, separate requests transport, no retry.

Authorization: user chose 'Send one diagnostic request now', explicitly allowing
this one local cooldown override on 26 October 2026. Fixed key prevents replay.
Google API Explorer UI is unavailable; this uses the SAME project/credentials.
"""
from datetime import datetime,timedelta,timezone
import json,os,uuid
import requests
from requests.adapters import HTTPAdapter
from psycopg.types.json import Jsonb
import youtube_auth as auth
import youtube_bot as bot
import youtube_chat as chat
import youtube_quota as quota

KEY='authorized-independent-help-diagnostic-2026-10-26-01'
project=os.environ['YOUTUBE_PROJECT_ID'];stream=os.environ['STREAM_URL']
chat.initialize()
with auth.locked():
    with quota.connect() as conn:
        existing=conn.execute('SELECT id,status,error_reason,response_id FROM youtube_chat_deliveries WHERE request_key=%s',(KEY,)).fetchone()
    if existing:
        print(json.dumps({'already_attempted':True,'delivery':existing,'replayed':False},default=str))
        raise SystemExit(0)
    if os.environ.get('YOUTUBE_CHAT_ENABLED','false').lower()!='true':
        raise ValueError('Posting disabled')
    target=chat.context(project,stream)
    token=auth.token(project)
    config=bot.command_config('!pomoc')
    if not config or not config[1]:raise ValueError('Help command disabled')
    text=bot.build_help_reply(stream,config)
    now=datetime.now(timezone.utc);expires=now+timedelta(seconds=120)
    request_id=str(uuid.uuid4());delivery_id=str(uuid.uuid4())
    channel=target['channel']['id'];chat_id=target['chat_id'];day=quota.api_day(now)
    cost=quota.POLICIES['cost.liveChatMessages.insert'][0]
    details={'authorization':'Explicit human approval for exactly one cooldown override',
             'transport':'requests.Session; HTTPAdapter(max_retries=0); redirects disabled',
             'api_explorer_used':False,'same_project_and_credentials':True,
             'fresh_text':text,'prepared_at':now.isoformat(),'expires_at':expires.isoformat(),
             'delivery_id':delivery_id,'automatic_retry':False,'request_key':KEY}
    with quota.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('youtube-project:'+project,))
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('youtube-account:'+channel,))
        limit=conn.execute('SELECT daily_limit FROM youtube_quota_projects WHERE project_id=%s',(project,)).fetchone()[0]
        used=conn.execute('SELECT COALESCE(sum(estimated_units),0) FROM youtube_api_requests WHERE project_id=%s AND api_day=%s',(project,day)).fetchone()[0]
        if used+cost>limit:raise quota.QuotaExceeded('Daily budget exceeded')
        attempts=conn.execute("SELECT count(*) FROM youtube_api_requests WHERE account_channel_id=%s AND method='liveChatMessages.insert' AND requested_at>now()-interval '30 seconds'",(channel,)).fetchone()[0]
        if attempts>=11:raise ValueError('Burst guard exceeded')
        paused=quota.rate_limits.paused(conn,channel,now)
        details['overridden_not_before']=paused.isoformat() if paused else None
        conn.execute('INSERT INTO youtube_chat_deliveries(id,request_key,project_id,channel_id,video_id,chat_id,message_text,source_kind,source_id,status) '
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,'diagnostic','user-authorized-!pomoc','sending')",(delivery_id,KEY,project,channel,chat.video_id(stream),chat_id,text))
        conn.execute('INSERT INTO youtube_delivery_expirations VALUES (%s,%s)',(delivery_id,expires))
        conn.execute('INSERT INTO youtube_api_requests(id,project_id,method,api_day,requested_at,estimated_units,status,account_channel_id,delivery_id) '
                     "VALUES (%s,%s,'liveChatMessages.insert',%s,%s,%s,'reserved',%s,%s)",(request_id,project,day,now,cost,channel,delivery_id))
        conn.execute('INSERT INTO youtube_operational_incidents(incident_key,request_id,details) VALUES (%s,%s,%s)',(KEY,request_id,Jsonb(details)))
    body={'snippet':{'liveChatId':chat_id,'type':'textMessageEvent','textMessageDetails':{'messageText':text}}}
    status,reason,response_id,http_status='unknown','diagnostic_outcome_unknown',None,None
    try:
        if datetime.now(timezone.utc)>=expires:raise ValueError('Fresh diagnostic expired before dispatch')
        with requests.Session() as session:
            session.mount('https://',HTTPAdapter(max_retries=0))
            with session.post('https://www.googleapis.com/youtube/v3/liveChat/messages',params={'part':'snippet'},
                              data=json.dumps(body).encode('utf-8'),
                              headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
                              timeout=(10,30),allow_redirects=False,stream=True) as response:
                http_status=response.status_code
                raw=response.raw.read(65537,decode_content=True)
                if len(raw)>65536:raise ValueError('Response too large')
                result=json.loads(raw)
                if 200<=http_status<300 and result.get('id'):
                    status,reason,response_id='sent',None,result['id']
                    quota.finish(request_id,'succeeded',http_status,response_id=response_id)
                else:
                    provider=quota.rate_limits.provider_error(result,response.headers.get('Retry-After'))
                    reason=provider['reason'];status='rejected' if 400<=http_status<500 else 'unknown'
                    details['provider']=provider
                    quota.finish(request_id,'failed',http_status,reason,provider=provider)
    except Exception as error:
        # No retry even on timeouts or ambiguous successful POSTs.
        details['exception_type']=type(error).__name__
        quota.finish(request_id,'unknown',http_status,error_reason=reason)
    details.update({'status':status,'reason':reason,'http_status':http_status,'response_id':response_id,
                    'request_id':request_id,'finished_at':datetime.now(timezone.utc).isoformat()})
    with quota.connect() as conn:
        conn.execute('UPDATE youtube_chat_deliveries SET status=%s,error_reason=%s,response_id=%s,completed_at=now() WHERE id=%s',(status,reason,response_id,delivery_id))
        conn.execute('UPDATE youtube_operational_incidents SET details=%s WHERE incident_key=%s',(Jsonb(details),KEY))
        state=conn.execute('SELECT not_before,consecutive_rejections FROM youtube_account_posting_state WHERE channel_id=%s',(channel,)).fetchone()
    print(json.dumps({'result':details,'account_state':state,'post_attempts':1,'automatic_retries':0},default=str,ensure_ascii=False,indent=2))
