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
import stream_lifecycle
from youtube_stream import batches

DISCLAIMER = 'zapytanie !ile może być użyte nie częściej niż raz na 10 minut'
HELP_DISCLAIMER = 'zapytanie !pomoc może być użyte nie częściej niż raz na 10 minut'
LINK_REPLY = 'ipduotest kropka pl'
LINK_DISCLAIMER = 'zapytanie !link może być użyte nie częściej niż raz na 10 minut'
RECORD_DISCLAIMER = 'zapytanie !rekord może być użyte nie częściej niż raz na 10 minut'
SOURCE_URL = 'https://github.com/bnowakow/Klawiatur-iPhone-Duo-fold-counter'
SOURCE_DISCLAIMER = 'zapytanie !kod-zródłowy może być użyte nie częściej niż raz na 10 minut'
DURATION_DISCLAIMER = 'zapytanie !jak-długo może być użyte nie częściej niż raz na 10 minut'
SAMSUNG_SOURCE_URL = 'https://cyfrowa.rp.pl/smartfony/art36496851-ile-faktycznie-wytrzyma-smartfon-polski-youtuber-postanowil-sprawdzic'
SAMSUNG_SUMMARY = ('Samsung Galaxy Z Flip3: 418 500 złożeń. Nadal działał, '
                   'ale nie domykał się. Źródło: cyfrowa.rp.pl.')
SAMSUNG_DISCLAIMER = 'zapytanie !samsung może być użyte nie częściej niż raz na 10 minut'
SCHEMA = """
CREATE TABLE IF NOT EXISTS youtube_bot_commands (
 command text PRIMARY KEY, handler_key text NOT NULL,
 enabled boolean NOT NULL DEFAULT true, cooldown_seconds integer NOT NULL CHECK(cooldown_seconds>=1),
 disclaimer text NOT NULL, max_message_age_seconds integer NOT NULL DEFAULT 120 CHECK(max_message_age_seconds>0),
 updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE youtube_bot_commands ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS youtube_bot_command_cache (
 command text PRIMARY KEY REFERENCES youtube_bot_commands(command),
 summary_pl text NOT NULL, source_url text NOT NULL, source_title text NOT NULL,
 facts jsonb NOT NULL, verified_on date NOT NULL, cached_at timestamptz NOT NULL DEFAULT now()
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
 min(received_at) AS first_received_at,max(received_at) AS last_received_at,
 count(*) FILTER(WHERE status='delayed') AS delayed
 FROM youtube_bot_command_events GROUP BY stream_url,chat_id,command;
CREATE OR REPLACE VIEW youtube_bot_command_daily_usage AS
 SELECT stream_url,chat_id,command,(received_at AT TIME ZONE 'Europe/Warsaw')::date AS usage_day,
 count(*) AS received,count(DISTINCT author_channel_id) AS unique_users,
 count(*) FILTER(WHERE status='sent') AS answered,
 count(*) FILTER(WHERE status='throttled') AS throttled,
 count(*) FILTER(WHERE status='stale') AS stale,
 count(*) FILTER(WHERE status='unavailable') AS unavailable,
 count(*) FILTER(WHERE status IN ('rejected','unknown')) AS failed_or_uncertain,
 count(*) FILTER(WHERE status='delayed') AS delayed
 FROM youtube_bot_command_events GROUP BY stream_url,chat_id,command,usage_day;
"""

SCHEMA += stream_lifecycle.SCHEMA + """
ALTER TABLE youtube_bot_command_events ADD COLUMN IF NOT EXISTS reply_not_before timestamptz;
ALTER TABLE youtube_bot_command_events ADD COLUMN IF NOT EXISTS reply_delay_seconds integer NOT NULL DEFAULT 0;
ALTER TABLE youtube_bot_command_events ADD COLUMN IF NOT EXISTS delay_claimed_at timestamptz;
CREATE INDEX IF NOT EXISTS youtube_bot_delayed_due ON youtube_bot_command_events(stream_url,reply_not_before)
 WHERE status='delayed';
ALTER TABLE youtube_bot_state ADD COLUMN IF NOT EXISTS actual_started_at timestamptz;
ALTER TABLE youtube_bot_state ADD COLUMN IF NOT EXISTS actual_ended_at timestamptz;
"""


def initialize():
    chat.initialize()
    with quota.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('youtube-bot-schema'))")
        conn.execute(SCHEMA)
        conn.execute('INSERT INTO youtube_bot_commands(command,handler_key,cooldown_seconds,disclaimer) '
                     "VALUES ('!ile','current_person',600,%s) ON CONFLICT DO NOTHING", (DISCLAIMER,))
        conn.execute('INSERT INTO youtube_bot_commands(command,handler_key,cooldown_seconds,disclaimer) '
                     "VALUES ('!pomoc','help',600,%s) ON CONFLICT DO NOTHING", (HELP_DISCLAIMER,))
        conn.execute('INSERT INTO youtube_bot_commands(command,handler_key,cooldown_seconds,disclaimer) '
                     "VALUES ('!link','static_link',600,%s) ON CONFLICT DO NOTHING", (LINK_DISCLAIMER,))
        conn.execute('INSERT INTO youtube_bot_commands(command,handler_key,cooldown_seconds,disclaimer) '
                     "VALUES ('!rekord','completed_record',600,%s) ON CONFLICT DO NOTHING", (RECORD_DISCLAIMER,))
        conn.execute('INSERT INTO youtube_bot_commands(command,handler_key,cooldown_seconds,disclaimer) '
                     "VALUES ('!kod-zródłowy','source_code',600,%s) ON CONFLICT DO NOTHING", (SOURCE_DISCLAIMER,))
        conn.execute('INSERT INTO youtube_bot_commands(command,handler_key,cooldown_seconds,disclaimer) '
                     "VALUES ('!jak-długo','stream_duration',600,%s) ON CONFLICT DO NOTHING", (DURATION_DISCLAIMER,))
        conn.execute('INSERT INTO youtube_bot_commands(command,handler_key,cooldown_seconds,disclaimer) '
                     "VALUES ('!samsung','cached_samsung',600,%s) ON CONFLICT DO NOTHING", (SAMSUNG_DISCLAIMER,))
        from psycopg.types.json import Jsonb
        conn.execute('INSERT INTO youtube_bot_command_cache '
                     '(command,summary_pl,source_url,source_title,facts,verified_on) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     ('!samsung',SAMSUNG_SUMMARY,SAMSUNG_SOURCE_URL,
                      'Ile faktycznie wytrzyma smartfon? Polski youtuber postanowił sprawdzić',
                      Jsonb({'model':'Samsung Galaxy Z Flip3','final_folds':418500,
                             'still_working_at_end':True,'could_close_fully_at_end':False,
                             'sand_added_after_folds':333333}), '2026-10-26'))
        for command,description in (('!ile','liczba złożeń i tempo'),('!pomoc','lista komend'),
                                    ('!samsung','wynik testu Samsunga'),('!link','adres strony testu'),
                                    ('!rekord','najlepsza zakończona zmiana'),
                                    ('!kod-zródłowy','repozytorium na GitHubie'),
                                    ('!jak-długo','czas trwania transmisji')):
            conn.execute("UPDATE youtube_bot_commands SET description=%s WHERE command=%s AND description=''",
                         (description,command))


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


def available_commands():
    with quota.connect() as conn:
        return conn.execute('SELECT command,description FROM youtube_bot_commands '
                            'WHERE enabled AND handler_key=ANY(%s) ORDER BY command', (list(HANDLERS),)).fetchall()


def build_help_reply(stream, config):
    commands = available_commands()
    listing = '; '.join(command+' — '+description if description else command for command,description in commands)
    text = 'Komendy: '+listing+'. '+config[3]
    if len(text)>200:
        text = 'Komendy: '+', '.join(command for command,_ in commands)+'. '+config[3]
    return chat.validate_message(text)


def build_samsung_reply(stream, config):
    with quota.connect() as conn:
        cached = conn.execute("SELECT summary_pl FROM youtube_bot_command_cache WHERE command='!samsung'").fetchone()
    if not cached:
        raise ValueError('Samsung source summary has not been cached')
    return chat.validate_message(cached[0]+' '+config[3])


def build_link_reply(stream, config):
    return chat.validate_message(LINK_REPLY)


def build_source_reply(stream, config):
    return chat.validate_message('Kod źródłowy: '+SOURCE_URL+' '+config[3])


def polish_duration(seconds):
    if seconds < 0:
        raise ValueError('Broadcast start is in the future')
    days, remaining = divmod(int(seconds),86400)
    hours, remaining = divmod(remaining,3600)
    minutes = remaining//60
    parts=[]
    for count, forms in [(days,('dzień','dni','dni')),
                         (hours,('godzina','godziny','godzin')),
                         (minutes,('minuta','minuty','minut'))]:
        if count:
            form=forms[0] if count==1 else forms[1] if count%10 in (2,3,4) and count%100 not in (12,13,14) else forms[2]
            parts.append(f'{count} {form}')
    return ', '.join(parts) if parts else 'poniżej minuty'


def build_duration_reply(stream, config):
    with quota.connect() as conn:
        row=conn.execute('SELECT actual_started_at,actual_ended_at FROM youtube_bot_state WHERE stream_url=%s',
                         (stream,)).fetchone()
    if not row or row[0] is None:
        return chat.validate_message('Brak potwierdzonego czasu rozpoczęcia transmisji. '+config[3])
    now=datetime.now(timezone.utc)
    end=row[1] or now
    elapsed=(end.astimezone(timezone.utc)-row[0].astimezone(timezone.utc)).total_seconds()
    label='Łączny czas zakończonej transmisji: ' if row[1] else 'Czas trwania transmisji: '
    return chat.validate_message(label+polish_duration(elapsed)+'. '+config[3])


def build_record_reply(stream, config):
    with quota.connect() as conn:
        row = conn.execute('SELECT s.last_counter-s.baseline_counter '
                           'FROM presenter_sessions s '
                           'WHERE s.stream_url=%s AND s.ended_at IS NOT NULL '
                           'AND s.folding_started_at IS NOT NULL AND s.ended_at>=s.folding_started_at '
                           "AND s.end_reason IS DISTINCT FROM 'manual_baseline_reset' "
                           'AND s.last_counter>s.baseline_counter '
                           'ORDER BY (s.last_counter-s.baseline_counter) DESC,s.ended_at,s.id LIMIT 1',
                           (stream,)).fetchone()
    if not row:
        return chat.validate_message('Brak zapisanej zakończonej zmiany ze złożeniami. '+config[3])
    return chat.validate_message(f'Rekord zakończonej zmiany: {row[0]} złożeń. '+config[3])


HANDLERS = {'current_person': build_reply, 'help': build_help_reply, 'cached_samsung': build_samsung_reply,
            'static_link': build_link_reply, 'completed_record': build_record_reply,
            'source_code': build_source_reply, 'stream_duration': build_duration_reply}


def parsed_command(item):
    if item.get('type') != 1:
        return None
    text = item.get('text', '').strip().casefold()
    return text if text.startswith('!') and len(text)<=80 and not any(c.isspace() for c in text) else None


def remember_state(stream, project, target):
    with quota.connect() as conn:
        conn.execute('INSERT INTO youtube_bot_state(stream_url,project_id,channel_id,chat_id,enabled_at,actual_started_at,actual_ended_at) '
                     'VALUES (%s,%s,%s,%s,now(),%s,%s) ON CONFLICT(stream_url) DO UPDATE SET '
                     'project_id=EXCLUDED.project_id,channel_id=EXCLUDED.channel_id, '
                     'actual_started_at=EXCLUDED.actual_started_at,actual_ended_at=EXCLUDED.actual_ended_at, '
                     'page_token=CASE WHEN youtube_bot_state.chat_id=EXCLUDED.chat_id '
                     'AND youtube_bot_state.channel_id=EXCLUDED.channel_id THEN youtube_bot_state.page_token ELSE NULL END, '
                     'enabled_at=CASE WHEN youtube_bot_state.chat_id=EXCLUDED.chat_id '
                     'AND youtube_bot_state.channel_id=EXCLUDED.channel_id THEN youtube_bot_state.enabled_at ELSE now() END, '
                     "chat_id=EXCLUDED.chat_id,connection_status='starting',heartbeat_at=now(),error_reason=NULL",
                     (stream,project,target['channel']['id'],target['chat_id'],
                      target.get('actual_started_at'),target.get('actual_ended_at')))
        row = conn.execute('SELECT enabled_at,page_token FROM youtube_bot_state WHERE stream_url=%s', (stream,)).fetchone()
    return row


def heartbeat(stream, status, error=None, page_token=None, batch=False):
    with quota.connect() as conn:
        conn.execute('UPDATE youtube_bot_state SET connection_status=%s,error_reason=%s,heartbeat_at=now(), '
                     'page_token=COALESCE(%s,page_token),last_batch_at=CASE WHEN %s THEN now() ELSE last_batch_at END '
                     'WHERE stream_url=%s', (status,error,page_token,batch,stream))


def video_finished(stream, ended_at, source):
    with quota.connect() as conn:
        stream_lifecycle.record_end(conn,stream,{'source':source,'actual_ended_at':ended_at})
        conn.execute('UPDATE youtube_bot_state SET actual_ended_at=COALESCE(actual_ended_at,%s) WHERE stream_url=%s',
                     (ended_at,stream))


def chat_finished(stream):
    with quota.connect() as conn:
        conn.execute("UPDATE youtube_bot_state SET connection_status='chat_closed',heartbeat_at=now(),"
                     "error_reason=NULL WHERE stream_url=%s",(stream,))
        conn.execute("UPDATE youtube_bot_command_events SET status='disabled',error_reason='chat_closed',completed_at=now() "
                     "WHERE stream_url=%s AND status='delayed'",(stream,))


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
    return process_claimed_command(project,stream,target,item,published,now,command,config,event_id)


def process_claimed_command(project,stream,target,item,published,now,command,config,event_id,allow_delay=True):
    chat_id=target['chat_id']
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
        until=quota.rate_limits.paused(conn,target['channel']['id'],now)
    if until:
        return complete('throttled','account_backoff')
    with quota.connect() as conn:
        cooldown = conn.execute('SELECT available_at FROM youtube_bot_cooldowns WHERE chat_id=%s AND command=%s', (chat_id,command)).fetchone()
    if cooldown and now < cooldown[0]:
        return complete('throttled')
    if allow_delay and item['author_channel_id']==target['channel']['id']:
        due=now+timedelta(seconds=60)
        with quota.connect() as conn:
            conn.execute("UPDATE youtube_bot_command_events SET status='delayed',reply_not_before=%s,"
                         'reply_delay_seconds=60 WHERE id=%s',(due,event_id))
        return {'event_id':event_id,'command':command,'status':'delayed',
                'reason':'same_posting_channel','reply_not_before':due.isoformat(),'delay_seconds':60}
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
                               source_kind='command',source_id=item['id'],
                               expires_at=published+timedelta(seconds=min(config[4],120)))
        outcome = complete(result['status'],result.get('error_reason'),text,result['id'])
    except Exception:
        outcome = complete('unknown', 'send_interrupted_or_unavailable', text)
    # Measure the next permitted reply from completion as well as initial claim.
    with quota.connect() as conn:
        conn.execute('UPDATE youtube_bot_cooldowns SET available_at=GREATEST(available_at,%s) '
                     'WHERE chat_id=%s AND command=%s AND last_event_id=%s',
                     (datetime.now(timezone.utc)+timedelta(seconds=policy[1]),chat_id,command,event_id))
    return outcome


def process_delayed_commands(project,stream,now=None):
    """Claim due instructions once; generate replies fresh, never retry a send."""
    now=now or datetime.now(timezone.utc)
    outcomes=[]
    for _ in range(20):
        with quota.connect() as conn:
            row=conn.execute('SELECT e.id,e.command,e.message_id,e.author_channel_id,e.published_at,'
                             'e.chat_id,b.channel_id,b.chat_id FROM youtube_bot_command_events e '
                             'LEFT JOIN youtube_bot_state b ON b.stream_url=e.stream_url AND b.project_id=%s '
                             "WHERE e.stream_url=%s AND e.status='delayed' AND e.reply_not_before<=%s "
                             'ORDER BY e.reply_not_before,e.id LIMIT 1 FOR UPDATE OF e SKIP LOCKED',
                             (project,stream,now)).fetchone()
            if not row:break
            event_id,command,message_id,author,published,chat_id,channel,current_chat=row
            conn.execute("UPDATE youtube_bot_command_events SET status='processing',delay_claimed_at=%s WHERE id=%s",(now,event_id))
        config=command_config(command)
        if not config or not channel or author!=channel or chat_id!=current_chat:
            with quota.connect() as conn:
                conn.execute("UPDATE youtube_bot_command_events SET status='disabled',error_reason='delayed_target_changed',completed_at=now() WHERE id=%s",(event_id,))
            outcomes.append({'event_id':event_id,'status':'disabled','error_reason':'delayed_target_changed'})
            continue
        item={'id':message_id,'author_channel_id':author}
        outcome=process_claimed_command(project,stream,{'channel':{'id':channel},'chat_id':chat_id},
                                       item,published,now,command,config,event_id,allow_delay=False)
        outcomes.append(outcome)
    return outcomes


def start_delayed_commands(project,stream,stop):
    def worker():
        while not stop.is_set():
            try:
                for result in process_delayed_commands(project,stream):
                    print(json.dumps(dict(result,event='delayed_command_completed')),flush=True)
            except Exception as error:
                print(json.dumps({'event':'delayed_command_error','level':'ERROR','reason':type(error).__name__}),flush=True)
            stop.wait(1)
    thread=threading.Thread(target=worker,name='youtube-delayed-commands',daemon=True)
    thread.start()
    return thread


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
        cached_content = conn.execute('SELECT * FROM youtube_bot_command_cache ORDER BY command').fetchall()
        usage = conn.execute('SELECT * FROM youtube_bot_command_usage WHERE (%s::text IS NULL OR stream_url=%s) ORDER BY command,stream_url',params).fetchall()
        daily = conn.execute('SELECT * FROM youtube_bot_command_daily_usage WHERE (%s::text IS NULL OR stream_url=%s) ORDER BY usage_day DESC LIMIT 100',params).fetchall()
        states = conn.execute('SELECT * FROM youtube_bot_state WHERE (%s::text IS NULL OR stream_url=%s) ORDER BY stream_url',params).fetchall()
        posting_backoff = conn.execute('SELECT p.*,p.not_before>now() AS active FROM youtube_account_posting_state p '
                                       'WHERE (%s::text IS NULL OR EXISTS (SELECT 1 FROM youtube_bot_state s '
                                       'WHERE s.stream_url=%s AND s.channel_id=p.channel_id))',params).fetchall()
        cooldowns = conn.execute('SELECT c.* FROM youtube_bot_cooldowns c WHERE (%s::text IS NULL OR EXISTS '
                                '(SELECT 1 FROM youtube_bot_command_events e WHERE e.chat_id=c.chat_id AND e.command=c.command AND e.stream_url=%s)) '
                                'ORDER BY available_at DESC LIMIT 100',params).fetchall()
        settings, notifications = [], []
        if conn.execute("SELECT to_regclass('youtube_stint_notifications') IS NOT NULL AS ready").fetchone()['ready']:
            settings = conn.execute('SELECT * FROM youtube_stint_notification_settings WHERE (%s::text IS NULL OR stream_url=%s)',params).fetchall()
            notifications = conn.execute('SELECT * FROM youtube_stint_notifications WHERE (%s::text IS NULL OR stream_url=%s) '
                                         'ORDER BY created_at DESC LIMIT 20',params).fetchall()
    return {'commands':commands,'cached_content':cached_content,'usage':usage,'daily_usage':daily,'workers':states,'cooldowns':cooldowns,
            'posting_backoff':posting_backoff,
            'stint_notification_settings':settings,'stint_notifications':notifications}


def run():
    project = os.getenv('YOUTUBE_PROJECT_ID', '')
    stream = os.getenv('STREAM_URL', 'https://www.youtube.com/watch?v=vaiHWiExDXg')
    if not project or os.getenv('YOUTUBE_CHAT_ENABLED','false').lower() != 'true':
        raise ValueError('Bot requires YOUTUBE_PROJECT_ID and YOUTUBE_CHAT_ENABLED=true')
    initialize()
    stop = threading.Event()
    shutdown = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: (stop.set(),shutdown.set()))
    signal.signal(signal.SIGINT, lambda *_: (stop.set(),shutdown.set()))
    with quota.connect() as conn:
        closed=conn.execute('SELECT connection_status FROM youtube_bot_state WHERE stream_url=%s',(stream,)).fetchone()
    if closed and closed[0]=='chat_closed':
        print(json.dumps({'event':'chat_completed','reason':'already_closed'}),flush=True)
        stop.wait()
        return
    with quota.connect() as leader:
        leader.autocommit = True
        owns = leader.execute('SELECT pg_try_advisory_lock(hashtext(%s))', ('youtube-bot-worker:'+stream,)).fetchone()[0]
        if not owns:
            raise ValueError('Another bot already owns this stream')
        with auth.locked():
            channel_id=auth.stored().get('channel',{}).get('id')
        if channel_id:
            with quota.connect() as conn:
                restored=quota.rate_limits.restore_history(conn,channel_id)
                until=quota.rate_limits.paused(conn,channel_id)
            if until:
                quota.rate_limits.log_pause('posting_cooldown_active',channel_id,until,
                                            restored_history_rows=restored)
        from youtube_stint_notifications import start as start_notifications
        notification_thread = start_notifications(project,stream,stop)
        delayed_thread = start_delayed_commands(project,stream,stop)
        backoff = 5
        chat_closed=False
        video_ended=False
        while not stop.is_set():
            try:
                # Session lock connection must remain alive for exclusive worker ownership.
                leader.execute('SELECT 1')
                with auth.locked():
                    target = chat.context(project, stream)
                enabled_at, cursor = remember_state(stream,project,target)
                if target.get('actual_ended_at'):
                    video_finished(stream,target['actual_ended_at'],'youtube.actualEndTime')
                    video_ended=True
                reconcile()
                heartbeat(stream,'post_live' if video_ended else 'streaming')
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
                            if result.get('error_reason') in ('liveChatEnded','liveChatDisabled','liveChatNotFound'):
                                raise stream_lifecycle.ChatClosed('YouTube no longer accepts chat replies')
                    heartbeat(stream,'post_live' if video_ended else 'streaming',page_token=batch['next_page_token'] or None,batch=True)
                    backoff = 5
                    if batch['offline_at']:
                        video_finished(stream,batch['offline_at'],'youtube.stream.offline_at')
                        video_ended=True
                if not stop.is_set():
                    stop.wait(30 if video_ended else 2)
            except Exception as error:
                reason = error.reason if isinstance(error,quota.YouTubeRequestFailed) else type(error).__name__
                if (isinstance(error,stream_lifecycle.ChatClosed) or
                        reason in ('liveChatEnded','liveChatDisabled','liveChatNotFound') or
                        (video_ended and reason=='grpc_not_found')):
                    chat_closed=True
                    chat_finished(stream)
                    stop.set()
                    break
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
        if not chat_closed:
            heartbeat(stream,'stopped')
        notification_thread.join(timeout=35)
        delayed_thread.join(timeout=35)
        if chat_closed:
            print(json.dumps({'event':'chat_completed','reason':'chat_closed'}),flush=True)
            shutdown.wait()  # Dormant until Docker stops the container.


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
        if not row or (row[0]!='chat_closed' and (not row[1] or row[0] not in ('streaming','starting','post_live'))):
            return 1
    else:
        initialize()
        print(json.dumps(statistics(),indent=2,default=str,ensure_ascii=False))
    return 0


if __name__=='__main__':
    sys.exit(main())
