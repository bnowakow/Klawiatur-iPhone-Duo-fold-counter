"""Persistent live-chat commands, global cooldowns and PostgreSQL usage statistics."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import signal
import sys
import threading
import youtube_auth as auth
import youtube_chat as chat
import youtube_quota as quota
from youtube_stream import batches

DISCLAIMER = 'zapytanie !ile może być użyte nie częściej niż raz na 10 minut'
SCHEMA = """
CREATE TABLE IF NOT EXISTS youtube_bot_commands (
 command text PRIMARY KEY, handler_key text NOT NULL,
 enabled boolean NOT NULL DEFAULT true, cooldown_seconds integer NOT NULL CHECK(cooldown_seconds>=1),
 disclaimer text NOT NULL, max_message_age_seconds integer NOT NULL DEFAULT 120 CHECK(max_message_age_seconds>0),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS youtube_bot_state (
 stream_url text PRIMARY KEY, project_id text NOT NULL, channel_id text NOT NULL,
 chat_id text NOT NULL, enabled_at timestamptz NOT NULL, page_token text,
 connection_status text NOT NULL DEFAULT 'starting', heartbeat_at timestamptz NOT NULL DEFAULT now(),
 last_batch_at timestamptz, error_reason text
);
CREATE TABLE IF NOT EXISTS youtube_bot_command_events (
 id bigserial PRIMARY KEY, stream_url text NOT NULL, chat_id text NOT NULL, message_id text NOT NULL,
 command text NOT NULL REFERENCES youtube_bot_commands(command), author_channel_id text NOT NULL,
 published_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
 status text NOT NULL, reply_text text, request_key text, delivery_id uuid,
 error_reason text, completed_at timestamptz,
 UNIQUE(chat_id,message_id)
);
CREATE INDEX IF NOT EXISTS youtube_bot_events_command_time ON youtube_bot_command_events(command,received_at);
CREATE TABLE IF NOT EXISTS youtube_bot_cooldowns (
 chat_id text NOT NULL, command text NOT NULL REFERENCES youtube_bot_commands(command),
 available_at timestamptz NOT NULL, last_claimed_at timestamptz NOT NULL,
 last_event_id bigint REFERENCES youtube_bot_command_events(id), PRIMARY KEY(chat_id,command)
);
CREATE OR REPLACE VIEW youtube_bot_command_usage AS
 SELECT stream_url,chat_id,command,count(*) AS received,
 count(DISTINCT author_channel_id) AS unique_users,
 count(*) FILTER(WHERE status='sent') AS answered,
 count(*) FILTER(WHERE status='throttled') AS throttled,
 count(*) FILTER(WHERE status='stale') AS stale,
 count(*) FILTER(WHERE status='unavailable') AS unavailable,
 count(*) FILTER(WHERE status='disabled') AS disabled,
 count(*) FILTER(WHERE status='rejected') AS rejected,
 count(*) FILTER(WHERE status='unknown') AS unknown,
 count(*) FILTER(WHERE status='processing') AS processing,
 min(received_at) AS first_received_at,max(received_at) AS last_received_at
 FROM youtube_bot_command_events GROUP BY stream_url,chat_id,command;
CREATE OR REPLACE VIEW youtube_bot_command_daily_usage AS
 SELECT stream_url,chat_id,command,(received_at AT TIME ZONE 'Europe/Warsaw')::date AS usage_day,
 count(*) AS received,count(DISTINCT author_channel_id) AS unique_users,
 count(*) FILTER(WHERE status='sent') AS answered,
 count(*) FILTER(WHERE status='throttled') AS throttled,
 count(*) FILTER(WHERE status='stale') AS stale,
 count(*) FILTER(WHERE status='unavailable') AS unavailable,
 count(*) FILTER(WHERE status IN ('rejected','unknown')) AS failed_or_uncertain
 FROM youtube_bot_command_events GROUP BY stream_url,chat_id,command,usage_day;
"""


def initialize():
    chat.initialize()
    with quota.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('youtube-bot-schema'))")
        conn.execute(SCHEMA)
        conn.execute('INSERT INTO youtube_bot_commands(command,handler_key,cooldown_seconds,disclaimer) '
                     "VALUES ('!ile','current_person',600,%s) ON CONFLICT DO NOTHING", (DISCLAIMER,))


def command_config(command):
    with quota.connect() as conn:
        row = conn.execute('SELECT handler_key,enabled,cooldown_seconds,disclaimer,max_message_age_seconds '
                           'FROM youtube_bot_commands WHERE command=%s', (command,)).fetchone()
    return row


def build_reply(stream, config):
    from utilities.current_person_message import generate_current_message
    # Use the same measurements/rounding as the clipboard, with room for the disclaimer.
    text = generate_current_message(stream, compact=True)
    return chat.validate_message(text+' '+config[3])


HANDLERS = {'current_person': build_reply}


def parsed_command(item):
    if item.get('type') != 1:
        return None
    text = item.get('text', '').strip().casefold()
    return text if text.startswith('!') and len(text)<=80 and not any(c.isspace() for c in text) else None


def remember_state(stream, project, target):
    with quota.connect() as conn:
        conn.execute('INSERT INTO youtube_bot_state(stream_url,project_id,channel_id,chat_id,enabled_at) '
                     'VALUES (%s,%s,%s,%s,now()) ON CONFLICT(stream_url) DO UPDATE SET '
                     'project_id=EXCLUDED.project_id,channel_id=EXCLUDED.channel_id, '
                     'page_token=CASE WHEN youtube_bot_state.chat_id=EXCLUDED.chat_id '
                     'AND youtube_bot_state.channel_id=EXCLUDED.channel_id THEN youtube_bot_state.page_token ELSE NULL END, '
                     'enabled_at=CASE WHEN youtube_bot_state.chat_id=EXCLUDED.chat_id '
                     'AND youtube_bot_state.channel_id=EXCLUDED.channel_id THEN youtube_bot_state.enabled_at ELSE now() END, '
                     "chat_id=EXCLUDED.chat_id,connection_status='starting',heartbeat_at=now(),error_reason=NULL",
                     (stream,project,target['channel']['id'],target['chat_id']))
        row = conn.execute('SELECT enabled_at,page_token FROM youtube_bot_state WHERE stream_url=%s', (stream,)).fetchone()
    return row


def heartbeat(stream, status, error=None, page_token=None, batch=False):
    with quota.connect() as conn:
        conn.execute('UPDATE youtube_bot_state SET connection_status=%s,error_reason=%s,heartbeat_at=now(), '
                     'page_token=COALESCE(%s,page_token),last_batch_at=CASE WHEN %s THEN now() ELSE last_batch_at END '
                     'WHERE stream_url=%s', (status,error,page_token,batch,stream))


def process_message(project, stream, target, enabled_at, item, now=None):
    """Durable inbox claim + atomic chat-wide cooldown; never retry a claimed command."""
    command = parsed_command(item)
    config = command_config(command) if command else None
    if not config or not item.get('id') or not item.get('author_channel_id'):
        return None
    try:
        published = datetime.fromisoformat(item['published_at'].replace('Z','+00:00'))
        if published.tzinfo is None:
            return None
    except (KeyError,TypeError,ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    # Ignore initial history from before bot activation; it isn't a new command usage.
    if published < enabled_at:
        return None
    chat_id = target['chat_id']
    with quota.connect() as conn:
        event = conn.execute('INSERT INTO youtube_bot_command_events '
            '(stream_url,chat_id,message_id,command,author_channel_id,published_at,received_at,status) '
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'processing') ON CONFLICT(chat_id,message_id) DO NOTHING RETURNING id",
            (stream,chat_id,item['id'],command,item['author_channel_id'],published,now)).fetchone()
    if not event:
        return {'status': 'duplicate'}
    event_id = event[0]
    def complete(status, reason=None, reply=None, delivery_id=None):
        with quota.connect() as conn:
            conn.execute('UPDATE youtube_bot_command_events SET status=%s,error_reason=%s,reply_text=COALESCE(%s,reply_text), '
                         'delivery_id=COALESCE(%s,delivery_id),completed_at=now() WHERE id=%s',
                         (status,reason,reply,delivery_id,event_id))
        return {'event_id': event_id, 'command': command, 'status': status, 'error_reason': reason}
    if not config[1]:
        return complete('disabled')
    if not 0 <= (now-published).total_seconds() <= config[4]:
        return complete('stale', 'message_too_old_or_future')
    with quota.connect() as conn:
        cooldown = conn.execute('SELECT available_at FROM youtube_bot_cooldowns WHERE chat_id=%s AND command=%s', (chat_id,command)).fetchone()
    if cooldown and now < cooldown[0]:
        return complete('throttled')
    handler = HANDLERS.get(config[0])
    if handler is None:
        return complete('unavailable', 'unknown_handler')
    try:
        text = handler(stream, config)
    except ValueError:
        return complete('unavailable', 'no_fresh_reliable_measurements_or_invalid_reply')
    # Serialize all users of this command in this chat. Failures after a claim
    # retain the cooldown, since a timed-out POST may already be visible.
    digest = hashlib.sha256((chat_id+'\0'+item['id']).encode()).hexdigest()
    request_key = 'command:'+digest
    with quota.connect() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('youtube-command:'+chat_id+':'+command,))
        # Fetch the policy again under the lock, so a disabled command cannot slip through.
        policy = conn.execute('SELECT enabled,cooldown_seconds FROM youtube_bot_commands WHERE command=%s FOR SHARE', (command,)).fetchone()
        if not policy[0]:
            status = 'disabled'
        else:
            cooldown = conn.execute('SELECT available_at FROM youtube_bot_cooldowns WHERE chat_id=%s AND command=%s', (chat_id,command)).fetchone()
            status = 'throttled' if cooldown and now < cooldown[0] else 'processing'
        if status == 'processing':
            conn.execute('INSERT INTO youtube_bot_cooldowns VALUES (%s,%s,%s,%s,%s) '
                         'ON CONFLICT(chat_id,command) DO UPDATE SET available_at=EXCLUDED.available_at, '
                         'last_claimed_at=EXCLUDED.last_claimed_at,last_event_id=EXCLUDED.last_event_id',
                         (chat_id,command,now+timedelta(seconds=policy[1]),now,event_id))
            conn.execute('UPDATE youtube_bot_command_events SET request_key=%s,reply_text=%s WHERE id=%s',
                         (request_key,text,event_id))
    if status != 'processing':
        return complete(status)
    try:
        with auth.locked():
            result = chat.send(project,stream,text,request_key,target['channel']['id'],chat_id,
                               source_kind='command',source_id=item['id'])
        outcome = complete(result['status'],result.get('error_reason'),text,result['id'])
    except Exception:
        outcome = complete('unknown', 'send_interrupted_or_unavailable', text)
    # Measure the next permitted reply from completion as well as initial claim.
    with quota.connect() as conn:
        conn.execute('UPDATE youtube_bot_cooldowns SET available_at=GREATEST(available_at,%s) '
                     'WHERE chat_id=%s AND command=%s AND last_event_id=%s',
                     (datetime.now(timezone.utc)+timedelta(seconds=policy[1]),chat_id,command,event_id))
    return outcome


def reconcile():
    chat.recover()
    with quota.connect() as conn:
        conn.execute("UPDATE youtube_bot_command_events e SET status=CASE WHEN d.status='sending' THEN 'unknown' ELSE d.status END, "
                     "delivery_id=d.id,completed_at=now(),error_reason='recovered_after_interruption' "
                     "FROM youtube_chat_deliveries d WHERE e.status='processing' AND e.request_key=d.request_key "
                     "AND e.received_at<now()-interval '5 minutes'")
        conn.execute("UPDATE youtube_bot_command_events SET status='unknown',completed_at=now(),error_reason='interrupted_before_delivery' "
                     "WHERE status='processing' AND received_at<now()-interval '5 minutes'")


def statistics(stream=None):
    from psycopg.rows import dict_row
    stream = stream or os.getenv('STREAM_URL') or None
    params = (stream,stream)
    with quota.connect() as conn:
        conn.row_factory = dict_row
        commands = conn.execute('SELECT * FROM youtube_bot_commands ORDER BY command').fetchall()
        usage = conn.execute('SELECT * FROM youtube_bot_command_usage WHERE (%s::text IS NULL OR stream_url=%s) ORDER BY command,stream_url',params).fetchall()
        daily = conn.execute('SELECT * FROM youtube_bot_command_daily_usage WHERE (%s::text IS NULL OR stream_url=%s) ORDER BY usage_day DESC LIMIT 100',params).fetchall()
        states = conn.execute('SELECT * FROM youtube_bot_state WHERE (%s::text IS NULL OR stream_url=%s) ORDER BY stream_url',params).fetchall()
        cooldowns = conn.execute('SELECT c.* FROM youtube_bot_cooldowns c WHERE (%s::text IS NULL OR EXISTS '
                                '(SELECT 1 FROM youtube_bot_command_events e WHERE e.chat_id=c.chat_id AND e.command=c.command AND e.stream_url=%s)) '
                                'ORDER BY available_at DESC LIMIT 100',params).fetchall()
    return {'commands':commands,'usage':usage,'daily_usage':daily,'workers':states,'cooldowns':cooldowns}


def run():
    project = os.getenv('YOUTUBE_PROJECT_ID', '')
    stream = os.getenv('STREAM_URL', 'https://www.youtube.com/watch?v=vaiHWiExDXg')
    if not project or os.getenv('YOUTUBE_CHAT_ENABLED','false').lower() != 'true':
        raise ValueError('Bot requires YOUTUBE_PROJECT_ID and YOUTUBE_CHAT_ENABLED=true')
    initialize()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with quota.connect() as leader:
        leader.autocommit = True
        owns = leader.execute('SELECT pg_try_advisory_lock(hashtext(%s))', ('youtube-bot-worker:'+stream,)).fetchone()[0]
        if not owns:
            raise ValueError('Another bot already owns this stream')
        from youtube_stint_notifications import start as start_notifications
        notification_thread = start_notifications(project,stream,stop)
        backoff = 5
        while not stop.is_set():
            try:
                # Session lock connection must remain alive for exclusive worker ownership.
                leader.execute('SELECT 1')
                with auth.locked():
                    target = chat.context(project, stream)
                enabled_at, cursor = remember_state(stream,project,target)
                reconcile()
                heartbeat(stream,'streaming')
                print(json.dumps({'event':'listening','chat_id':target['chat_id'],'channel':target['channel']['title']}), flush=True)
                for batch in batches(project,target['chat_id'],cursor,stop):
                    if stop.is_set():
                        break
                    leader.execute('SELECT 1')
                    for item in batch['items']:
                        if stop.is_set():
                            break
                        result = process_message(project,stream,target,enabled_at,item)
                        if result:
                            print(json.dumps(result), flush=True)
                    heartbeat(stream,'streaming',page_token=batch['next_page_token'] or None,batch=True)
                    backoff = 5
                    if batch['offline_at']:
                        break
                if not stop.is_set():
                    stop.wait(2)
            except Exception as error:
                reason = error.reason if isinstance(error,quota.YouTubeRequestFailed) else type(error).__name__
                try:
                    if reason == 'grpc_invalid_argument':
                        # An expired server cursor must not permanently strand the reader.
                        with quota.connect() as conn:
                            conn.execute('UPDATE youtube_bot_state SET page_token=NULL WHERE stream_url=%s', (stream,))
                    heartbeat(stream,'backoff',reason)
                except Exception:
                    pass
                print(json.dumps({'event':'worker_backoff','reason':reason,'seconds':backoff}), flush=True)
                # If the leader connection is lost, exit; Docker reacquires ownership.
                if leader.closed or leader.broken:
                    raise RuntimeError('Worker lock connection was lost') from None
                stop.wait(backoff)
                backoff = min(300,backoff*2)
        heartbeat(stream,'stopped')
        notification_thread.join(timeout=35)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('run','init','stats','health'),default='run',nargs='?')
    args = parser.parse_args()
    if args.command=='run':
        run()
    elif args.command=='health':
        with quota.connect() as conn:
            row=conn.execute('SELECT connection_status,heartbeat_at>now()-interval \'20 minutes\' '
                             'FROM youtube_bot_state WHERE stream_url=%s', (os.getenv('STREAM_URL','https://www.youtube.com/watch?v=vaiHWiExDXg'),)).fetchone()
        if not row or not row[1] or row[0] not in ('streaming','starting'):
            return 1
    else:
        initialize()
        print(json.dumps(statistics(),indent=2,default=str,ensure_ascii=False))
    return 0


if __name__=='__main__':
    sys.exit(main())
