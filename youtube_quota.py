"""Persist YouTube limits and conservatively account for official API requests.

No OAuth authorization or automatic posting is started by this module.
"""
import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import uuid
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo('America/Los_Angeles')
VERIFIED_ON = date(2026, 10, 26)
QUOTA_SOURCE = 'https://developers.google.com/youtube/data/determine_quota_cost'
CHAT_SOURCE = 'https://support.google.com/youtube/answer/15270973?hl=en'
OAUTH_SOURCE = 'https://support.google.com/cloud/answer/15549945?hl=en'
POLICIES = {
    'default_daily_units': (10000, QUOTA_SOURCE),
    'daily_reset_timezone': ('America/Los_Angeles', QUOTA_SOURCE),
    'cost.liveChatMessages.insert': (20, QUOTA_SOURCE),
    'cost.liveChatMessages.list': (5, QUOTA_SOURCE),
    'cost.liveChatMessages.streamList': (5, QUOTA_SOURCE),
    'cost.videos.list': (1, QUOTA_SOURCE),
    'cost.channels.list': (1, QUOTA_SOURCE),
    'chat_message_characters': (200, CHAT_SOURCE),
    'viewer_messages_per_window': (11, CHAT_SOURCE),
    'viewer_window_seconds': (30, CHAT_SOURCE),
    'oauth_testing_users': (100, OAUTH_SOURCE),
    'oauth_external_testing_authorization_days': (7, OAUTH_SOURCE),
}
METHODS = {
    'liveChatMessages.insert': ('POST', 'liveChat/messages'),
    'liveChatMessages.list': ('GET', 'liveChat/messages'),
    'videos.list': ('GET', 'videos'),
    'channels.list': ('GET', 'channels'),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS youtube_api_policies (
 policy_key text NOT NULL, verified_on date NOT NULL,
 value jsonb NOT NULL, source_url text NOT NULL,
 PRIMARY KEY (policy_key,verified_on)
);
CREATE TABLE IF NOT EXISTS youtube_quota_projects (
 project_id text PRIMARY KEY, daily_limit integer NOT NULL CHECK (daily_limit>0),
 configured_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS youtube_api_requests (
 id uuid PRIMARY KEY, project_id text NOT NULL REFERENCES youtube_quota_projects(project_id),
 method text NOT NULL, api_day date NOT NULL, requested_at timestamptz NOT NULL,
 completed_at timestamptz, estimated_units integer NOT NULL CHECK (estimated_units>0),
 status text NOT NULL CHECK (status IN ('reserved','succeeded','failed','unknown')),
 account_channel_id text, http_status integer, error_reason text, response_id text
);
CREATE INDEX IF NOT EXISTS youtube_api_requests_project_day
 ON youtube_api_requests(project_id,api_day);
CREATE INDEX IF NOT EXISTS youtube_api_requests_account_time
 ON youtube_api_requests(account_channel_id,requested_at);
CREATE OR REPLACE VIEW youtube_daily_usage AS
 SELECT project_id,api_day,count(*) AS attempts,sum(estimated_units) AS estimated_units,
 count(*) FILTER (WHERE status='succeeded') AS succeeded,
 count(*) FILTER (WHERE status='failed') AS failed,
 count(*) FILTER (WHERE status IN ('unknown','reserved')) AS uncertain
 FROM youtube_api_requests GROUP BY project_id,api_day;
"""


class QuotaExceeded(RuntimeError):
    pass


class ChatRateLimited(RuntimeError):
    pass


class YouTubeRequestFailed(RuntimeError):
    pass


def connect():
    import psycopg
    return psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=10)


def ensure_schema(conn):
    from psycopg.types.json import Jsonb
    conn.execute(SCHEMA)
    for key,(value,source) in POLICIES.items():
        conn.execute('INSERT INTO youtube_api_policies VALUES (%s,%s,%s,%s) '
                     'ON CONFLICT DO NOTHING',(key,VERIFIED_ON,Jsonb(value),source))


def api_day(now):
    if now.tzinfo is None:
        raise ValueError('Quota timestamps must include a timezone')
    return now.astimezone(PACIFIC).date()


def reset_at(now):
    return datetime.combine(api_day(now)+timedelta(days=1),time.min,PACIFIC)


def configure(project_id, daily_limit=10000):
    if not project_id.strip() or len(project_id)>128 or daily_limit<1:
        raise ValueError('A project ID and positive daily quota are required')
    with connect() as conn:
        ensure_schema(conn)
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('youtube-project:'+project_id,))
        conn.execute('INSERT INTO youtube_quota_projects(project_id,daily_limit) VALUES (%s,%s) '
                     'ON CONFLICT (project_id) DO UPDATE SET daily_limit=EXCLUDED.daily_limit,configured_at=now()',
                     (project_id,daily_limit))


def reserve(project_id, method, account_channel_id=None, now=None):
    """Commit budget reservation before network I/O. No implicit refunds."""
    now = now or datetime.now(timezone.utc)
    day=api_day(now)
    cost=POLICIES.get('cost.'+method)
    if cost is None:
        raise ValueError('Unsupported YouTube method')
    if method=='liveChatMessages.insert' and not account_channel_id:
        raise ValueError('Posting requires the authorized YouTube channel ID')
    request_id=str(uuid.uuid4())
    with connect() as conn:
        ensure_schema(conn)
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('youtube-project:'+project_id,))
        row=conn.execute('SELECT daily_limit FROM youtube_quota_projects WHERE project_id=%s',
                         (project_id,)).fetchone()
        if not row:
            raise ValueError('Configure the real Google Cloud project before making API requests')
        used=conn.execute('SELECT COALESCE(sum(estimated_units),0) FROM youtube_api_requests '
                          'WHERE project_id=%s AND api_day=%s',(project_id,day)).fetchone()[0]
        if used+cost[0]>row[0]:
            raise QuotaExceeded('Local daily YouTube quota budget would be exceeded')
        if method=='liveChatMessages.insert':
            conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('youtube-account:'+account_channel_id,))
            attempts=conn.execute("SELECT count(*) FROM youtube_api_requests WHERE account_channel_id=%s "
                                  "AND method='liveChatMessages.insert' AND requested_at>%s AND requested_at<=%s",
                                  (account_channel_id,now-timedelta(seconds=30),now)).fetchone()[0]
            if attempts>=11:
                raise ChatRateLimited('Local account guard: 11 posting attempts in 30 seconds')
        conn.execute('INSERT INTO youtube_api_requests '
                     '(id,project_id,method,api_day,requested_at,estimated_units,status,account_channel_id) '
                     "VALUES (%s,%s,%s,%s,%s,%s,'reserved',%s)",
                     (request_id,project_id,method,day,now,cost[0],account_channel_id))
    return request_id


def finish(request_id, status, http_status=None, error_reason=None, response_id=None):
    if status not in ('succeeded','failed','unknown'):
        raise ValueError('Invalid completion status')
    with connect() as conn:
        conn.execute('UPDATE youtube_api_requests SET completed_at=now(),status=%s,http_status=%s, '
                     'error_reason=%s,response_id=%s WHERE id=%s AND completed_at IS NULL',
                     (status,http_status,error_reason,response_id,request_id))


def tracked_request(project_id, method, access_token, params=None, body=None, account_channel_id=None):
    """Official API transport for a future OAuth client; never log credentials.

    No automatic retries: a timed-out POST may already have published a message.
    """
    if method not in METHODS:
        raise ValueError('Unsupported HTTP API method')
    if not access_token:
        raise ValueError('An OAuth access token is required')
    verb,path=METHODS[method]
    if method=='liveChatMessages.insert':
        snippet=(body or {}).get('snippet',{})
        message=snippet.get('textMessageDetails',{}).get('messageText','')
        if (snippet.get('type')!='textMessageEvent' or not snippet.get('liveChatId') or
                not isinstance(message,str) or not message.strip() or len(message)>200):
            raise ValueError('A live-chat ID and text message of 1–200 characters are required')
    data=json.dumps(body).encode() if body is not None else None
    request=Request('https://www.googleapis.com/youtube/v3/'+path+'?'+urlencode(params or {}),
                    data=data,method=verb,headers={'Authorization':'Bearer '+access_token,
                                                  'Content-Type':'application/json'})
    request_id=reserve(project_id,method,account_channel_id)
    try:
        with urlopen(request,timeout=30) as response:
            raw=response.read(2*1024*1024+1)
            if len(raw)>2*1024*1024:
                raise ValueError('API response too large')
            result=json.loads(raw)
            http_status=response.status
    except HTTPError as error:
        reason='http_error'
        try:
            reply=json.loads(error.read(65536))
            value=reply['error']['errors'][0]['reason']
            if isinstance(value,str) and value.isalnum():reason=value[:100]
        except (ValueError,KeyError,IndexError,TypeError):
            pass
        finish(request_id,'failed',error.code,reason)
        raise YouTubeRequestFailed(f'YouTube returned HTTP {error.code}: {reason}; request {request_id}') from None
    except Exception:
        finish(request_id,'unknown',error_reason='network_or_response_failure')
        raise YouTubeRequestFailed(f'Request outcome unknown; do not blindly retry a POST: {request_id}') from None
    # If this write fails after a successful POST, the durable reservation
    # remains uncertain; it is still counted and must not trigger a retry.
    finish(request_id,'succeeded',http_status,response_id=result.get('id'))
    return result


def report(project_id=None, now=None):
    now=now or datetime.now(timezone.utc)
    result={'api_day':api_day(now).isoformat(),'resets_at':reset_at(now).isoformat(),
            'policy_verified_on':VERIFIED_ON.isoformat(),
            'documented_limits':{k:v[0] for k,v in POLICIES.items()},
            'project_id':project_id,'accounting':'local conservative estimate; Google Cloud is authoritative',
            'oauth_connected':False}
    with connect() as conn:
        ensure_schema(conn)
        if project_id:
            budget=conn.execute('SELECT daily_limit FROM youtube_quota_projects WHERE project_id=%s',
                                (project_id,)).fetchone()
            if not budget:raise ValueError('Google Cloud project has not been configured for accounting')
            row=conn.execute('SELECT attempts,estimated_units,succeeded,failed,uncertain '
                             'FROM youtube_daily_usage WHERE project_id=%s AND api_day=%s',
                             (project_id,api_day(now))).fetchone() or (0,0,0,0,0)
            result.update(daily_limit=budget[0],attempts=row[0],estimated_used_units=row[1],
                          succeeded=row[2],failed=row[3],uncertain=row[4],
                          remaining_budget_units=max(0,budget[0]-row[1]),
                          theoretical_posts_remaining=max(0,budget[0]-row[1])//20)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('init','configure','status'),default='status',nargs='?')
    parser.add_argument('--project',default=os.getenv('YOUTUBE_PROJECT_ID') or None)
    parser.add_argument('--daily-limit',type=int,default=int(os.getenv('YOUTUBE_DAILY_QUOTA_UNITS','10000')))
    args=parser.parse_args()
    if args.command=='configure':
        if not args.project:parser.error('--project or YOUTUBE_PROJECT_ID is required')
        configure(args.project,args.daily_limit)
    print(json.dumps(report(args.project),indent=2))


if __name__=='__main__':
    main()
