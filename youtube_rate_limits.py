"""Persistent posting backpressure and diagnostic context; never queues a send."""
from datetime import datetime, timedelta, timezone
import math
import json
import random
import re
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


def log_pause(event, channel_id, until, *, now=None, **details):
    """Flush one structured stdout record for Compose log monitoring."""
    now = now or datetime.now(timezone.utc)
    remaining = max(0, math.ceil((until-now).total_seconds()))
    hours, remainder = divmod(remaining, 3600)
    minutes, seconds = divmod(remainder, 60)
    print(json.dumps({**details, 'event':event, 'level':'ERROR',
        'timestamp':now.isoformat(), 'channel_id':channel_id,
        'not_before':until.isoformat(),
        'not_before_warsaw':until.astimezone(ZoneInfo('Europe/Warsaw')).isoformat(),
        'retry_in_seconds':remaining,
        'retry_in':f'{hours:02d}:{minutes:02d}:{seconds:02d}',
        'message':f'YouTube posting paused; next eligible attempt in {hours:02d}:{minutes:02d}:{seconds:02d}. '
                  'Only fresh messages may be posted; YouTube may still reject them.'}), flush=True)

POLICY = {'backoff_seconds':[600,1200,2400,3600], 'jitter_fraction':0.05,
          'max_message_age_seconds':120, 'resend_old_messages':False,
          'read_reconnect_recommendation':'Keep streams open; honor provider pacing and back off on reconnects.',
          'thresholds_are_app_policy_not_guaranteed_youtube_limits':True,
          'sources':['https://developers.google.com/youtube/data/live/reference/rest/v3/liveChatMessages/insert',
                     'https://developers.google.com/youtube/data/live/reference/rest/v3/liveChatMessages/streamList']}
SCHEMA = """
CREATE TABLE IF NOT EXISTS youtube_posting_policies (
 policy_key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS youtube_account_posting_state (
 channel_id text PRIMARY KEY, not_before timestamptz NOT NULL,
 consecutive_rejections integer NOT NULL DEFAULT 0,
 last_rate_limit_at timestamptz, last_success_at timestamptz,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS youtube_posting_backoff_events (
 request_id uuid PRIMARY KEY REFERENCES youtube_api_requests(id), channel_id text NOT NULL,
 observed_at timestamptz NOT NULL, delay_seconds integer NOT NULL,
 not_before timestamptz NOT NULL, consecutive_rejections integer NOT NULL,
 details jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS youtube_api_error_details (
 request_id uuid PRIMARY KEY REFERENCES youtube_api_requests(id),
 http_status integer, provider_message text, provider_domain text,
 retry_after_raw text, retry_after_seconds integer, details jsonb NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS youtube_operational_incidents (
 incident_key text PRIMARY KEY, request_id uuid REFERENCES youtube_api_requests(id),
 recorded_at timestamptz NOT NULL DEFAULT now(), details jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS youtube_delivery_expirations (
 delivery_id uuid PRIMARY KEY REFERENCES youtube_chat_deliveries(id), expires_at timestamptz NOT NULL
);
"""
# Delivery FK is created by youtube_chat after its own table exists.
SCHEMA, DELIVERY_SCHEMA = SCHEMA.split('CREATE TABLE IF NOT EXISTS youtube_delivery_expirations',1)
DELIVERY_SCHEMA = 'CREATE TABLE IF NOT EXISTS youtube_delivery_expirations'+DELIVERY_SCHEMA


def seed_policy(conn):
    from psycopg.types.json import Jsonb
    conn.execute('INSERT INTO youtube_posting_policies(policy_key,value) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                 ('posting_backpressure',Jsonb(POLICY)))


def paused(conn, channel_id, now=None):
    now=now or datetime.now(timezone.utc)
    row=conn.execute('SELECT not_before FROM youtube_account_posting_state WHERE channel_id=%s AND not_before>%s',
                     (channel_id,now)).fetchone()
    return row[0] if row else None


def restore_history(conn, channel_id):
    """Initialize missing account state from durable requests, without sending."""
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('youtube-account:'+channel_id,))
    if conn.execute('SELECT 1 FROM youtube_account_posting_state WHERE channel_id=%s',(channel_id,)).fetchone():
        return 0
    rows=conn.execute("SELECT id,status,error_reason,COALESCE(completed_at,requested_at) FROM youtube_api_requests "
                      "WHERE account_channel_id=%s AND method='liveChatMessages.insert' "
                      "AND (status='succeeded' OR error_reason='rateLimitExceeded') ORDER BY requested_at,id",(channel_id,)).fetchall()
    for request_id,status,reason,observed in rows:
        record_result(conn,request_id,'liveChatMessages.insert',channel_id,status,reason,now=observed)
    return len(rows)


def retry_seconds(value, now=None):
    if not value:return None
    now=now or datetime.now(timezone.utc)
    try:
        number=float(value)
        if not math.isfinite(number):return None
        return max(0,math.ceil(number))
    except ValueError:
        try:
            stamp=parsedate_to_datetime(value)
            if stamp.tzinfo is None:return None
            return max(0,math.ceil((stamp-now).total_seconds()))
        except (TypeError,ValueError,OverflowError):return None


def redact(text):
    text=str(text or '')[:4000]
    text=re.sub(r'(?i)Bearer\s+\S+|ya29\.[\w.\-]+|(?i:access_token|refresh_token|client_secret|key)=[^\s&]+',
                '[redacted]',text)
    return text


def provider_error(reply, retry_after=None, now=None):
    error=reply.get('error',{}) if isinstance(reply,dict) else {}
    errors=error.get('errors',[]) if isinstance(error,dict) else []
    first=errors[0] if isinstance(errors,list) and errors and isinstance(errors[0],dict) else {}
    reason=first.get('reason','http_error')
    if not isinstance(reason,str) or not reason.isalnum():reason='http_error'
    return {'reason':reason[:100], 'provider_message':redact(error.get('message','') if isinstance(error,dict) else ''),
            'provider_domain':redact(first.get('domain',''))[:200],
            'retry_after_raw':redact(retry_after)[:200] or None,
            'retry_after_seconds':retry_seconds(retry_after,now),
            'provider_errors':[{k:redact(e[k])[:1000] for k in ('reason','domain','message') if k in e}
                               for e in errors if isinstance(e,dict)] if isinstance(errors,list) else []}


def record_result(conn, request_id, method, channel_id, status, reason, provider=None, now=None):
    from psycopg.types.json import Jsonb
    now=now or datetime.now(timezone.utc)
    if method!='liveChatMessages.insert' or not channel_id:return
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('youtube-account:'+channel_id,))
    if status=='succeeded':
        conn.execute('INSERT INTO youtube_account_posting_state(channel_id,not_before,last_success_at) VALUES (%s,%s,%s) '
                     'ON CONFLICT(channel_id) DO UPDATE SET consecutive_rejections=0,last_success_at=EXCLUDED.last_success_at,'
                     'not_before=EXCLUDED.not_before,updated_at=now()', (channel_id,now,now))
    elif reason=='rateLimitExceeded':
        row=conn.execute('SELECT consecutive_rejections,not_before FROM youtube_account_posting_state WHERE channel_id=%s',
                         (channel_id,)).fetchone()
        count=(row[0] if row else 0)+1
        delay=POLICY['backoff_seconds'][min(count-1,len(POLICY['backoff_seconds'])-1)]
        # Jitter only lengthens the wait; provider Retry-After can require longer.
        delay=math.ceil(delay*(1+random.uniform(0,POLICY['jitter_fraction'])))
        delay=max(delay,(provider or {}).get('retry_after_seconds') or 0)
        until=max(now+timedelta(seconds=delay),row[1] if row else now)
        conn.execute('INSERT INTO youtube_account_posting_state(channel_id,not_before,consecutive_rejections,last_rate_limit_at) '
                     'VALUES (%s,%s,%s,%s) ON CONFLICT(channel_id) DO UPDATE SET not_before=EXCLUDED.not_before,'
                     'consecutive_rejections=EXCLUDED.consecutive_rejections,last_rate_limit_at=EXCLUDED.last_rate_limit_at,updated_at=now()',
                     (channel_id,until,count,now))
        conn.execute('INSERT INTO youtube_posting_backoff_events VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (request_id,channel_id,now,delay,until,count,Jsonb({'reason':reason,'policy':POLICY,'provider':provider or {}})))
