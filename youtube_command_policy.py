"""Explicit one-minute command trial; a posting rejection restores ten minutes."""
from datetime import datetime,timezone
import json
import uuid
from psycopg.types.json import Jsonb


def policy_key(channel):return 'command_cooldown_adaptation:'+channel


def apply_seconds(conn,channel,seconds,reason,request_id=None):
    duration='minutę' if seconds==60 else '10 minut'
    conn.execute("UPDATE youtube_bot_commands SET cooldown_seconds=%s,disclaimer="
                 "'zapytanie '||command||' może być użyte nie częściej niż raz na '||%s",
                 (seconds,duration))
    if seconds==600:
        conn.execute("UPDATE youtube_bot_cooldowns SET available_at=GREATEST(available_at,last_claimed_at+interval '10 minutes')")
    else:
        conn.execute("UPDATE youtube_bot_cooldowns c SET available_at=LEAST(c.available_at,"
                     "GREATEST(c.last_claimed_at,COALESCE(e.completed_at,c.last_claimed_at))+interval '1 minute') "
                     'FROM youtube_bot_command_events e WHERE e.id=c.last_event_id')
    details={'event':'youtube_command_cooldown_changed','level':'ERROR' if seconds==600 else 'INFO',
             'channel_id':channel,'cooldown_seconds':seconds,'reason':reason,
             'request_id':str(request_id) if request_id else None,'at':datetime.now(timezone.utc).isoformat(),
             'account_backoff_changed':False,'old_messages_retried':False}
    conn.execute('INSERT INTO youtube_operational_incidents(incident_key,request_id,details) VALUES (%s,%s,%s)',
                 ('command-cooldown-change-'+str(uuid.uuid4()),request_id,Jsonb(details)))
    return details


def enable_trial(conn,channel):
    # The same row lock serializes operator activation with provider rejection.
    key=policy_key(channel)
    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('youtube-account:'+channel,))
    conn.execute('INSERT INTO youtube_posting_policies(policy_key,value) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                 (key,Jsonb({})))
    conn.execute('SELECT value FROM youtube_posting_policies WHERE policy_key=%s FOR UPDATE',(key,))
    details=apply_seconds(conn,channel,60,'user_requested_one_minute_trial')
    value={'enabled':True,'active_seconds':60,'fallback_seconds':600,
           'revert_on':'liveChatMessages.insert rateLimitExceeded','activated_at':details['at'],
           'automatically_reenable_trial':False}
    conn.execute('UPDATE youtube_posting_policies SET value=%s,updated_at=now() WHERE policy_key=%s',
                 (Jsonb(value),key))
    return details


def on_post_rate_limit(conn,channel,request_id):
    key=policy_key(channel)
    row=conn.execute('SELECT value FROM youtube_posting_policies WHERE policy_key=%s FOR UPDATE',(key,)).fetchone()
    if not row or not isinstance(row[0],dict) or not row[0].get('enabled') or row[0].get('active_seconds')!=60:return None
    details=apply_seconds(conn,channel,600,'provider_rateLimitExceeded',request_id)
    value=dict(row[0],active_seconds=600,reverted_at=details['at'],trigger_request_id=str(request_id))
    conn.execute('UPDATE youtube_posting_policies SET value=%s,updated_at=now() WHERE policy_key=%s',
                 (Jsonb(value),key))
    return details
