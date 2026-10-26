"""Manual YouTube live-chat delivery; reusable by future command handlers."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit
import uuid
import youtube_auth as auth
import youtube_quota as quota

SCHEMA = """
CREATE TABLE IF NOT EXISTS youtube_chat_deliveries (
 id uuid PRIMARY KEY, request_key text UNIQUE NOT NULL,
 project_id text NOT NULL, channel_id text NOT NULL, video_id text NOT NULL,
 chat_id text NOT NULL, message_text text NOT NULL, source_kind text NOT NULL,
 source_id text, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 status text NOT NULL CHECK(status IN ('sending','sent','rejected','unknown')),
 error_reason text, response_id text
);
"""


def initialize():
    with quota.connect() as conn:
        quota.ensure_schema(conn)
        conn.execute(SCHEMA)


def video_id(url):
    parsed = urlsplit(url)
    if parsed.hostname in ('youtube.com', 'www.youtube.com') and parsed.path == '/watch':
        result = parse_qs(parsed.query).get('v', [''])[0]
    elif parsed.hostname == 'youtu.be':
        result = parsed.path.lstrip('/')
    else:
        raise ValueError('Expected a YouTube watch URL')
    if len(result) != 11 or not all(c.isascii() and (c.isalnum() or c in '_-') for c in result):
        raise ValueError('Invalid YouTube video ID')
    return result


def validate_message(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 200:
        raise ValueError('Message must contain 1–200 characters')
    return text


def identify(project, access_token):
    result = quota.tracked_request(project, 'channels.list', access_token,
                                   params={'part': 'snippet', 'mine': 'true'})
    items = result.get('items', [])
    if len(items) != 1:
        raise ValueError('Expected one authorized YouTube channel; reconnect with the intended channel')
    identity = {'id': items[0]['id'], 'title': items[0]['snippet']['title']}
    value = auth.stored()
    previous = value.get('channel')
    if previous and previous['id'] != identity['id']:
        raise ValueError('Authorized channel changed; disconnect and reconnect explicitly')
    value['channel'] = identity
    auth.save(value)
    return identity


def context(project, stream):
    access_token = auth.token(project)
    # Verify identity rather than silently relying on a stale file. Both reads are accounted.
    channel = identify(project, access_token)
    video = video_id(stream)
    result = quota.tracked_request(project, 'videos.list', access_token,
                                   params={'part': 'liveStreamingDetails,snippet', 'id': video})
    items = result.get('items', [])
    chat = items[0].get('liveStreamingDetails', {}).get('activeLiveChatId') if len(items) == 1 else None
    if not chat:
        raise ValueError('The configured video has no active live chat')
    return {'project_id': project, 'channel': channel, 'video_id': video, 'chat_id': chat}


def deliveries(limit=20):
    initialize()
    with quota.connect() as conn:
        rows = conn.execute('SELECT id,request_key,status,response_id,error_reason,created_at '
                            'FROM youtube_chat_deliveries ORDER BY created_at DESC LIMIT %s', (limit,)).fetchall()
    return [dict(zip(('id','request_key','status','response_id','error_reason','created_at'), row)) for row in rows]


def existing(request_key, project, video, channel, chat, text, source_kind, source_id):
    """Look up a previously claimed key before making any Google calls."""
    with quota.connect() as conn:
        row = conn.execute('SELECT id,status,response_id,project_id,video_id,channel_id,chat_id,message_text,source_kind,source_id '
                           'FROM youtube_chat_deliveries WHERE request_key=%s', (request_key,)).fetchone()
    if not row:
        return None
    if tuple(row[3:]) != (project,video,channel,chat,text,source_kind,source_id):
        raise ValueError('Request key already belongs to a different delivery')
    return {'id': str(row[0]), 'status': row[1], 'response_id': row[2], 'duplicate_request': True}


def send(project, stream, text, request_key, expected_channel, expected_chat,
         source_kind='manual', source_id=None):
    """Caller holds auth.locked(). Future bots pass source_kind='command', source_id=message ID.

    A request key is never resent, including when its previous outcome is unknown.
    """
    validate_message(text)
    if not request_key or len(request_key)>200 or not expected_channel or not expected_chat:
        raise ValueError('Request key, expected channel and expected chat are required')
    initialize()
    video = video_id(stream)
    previous = existing(request_key, project, video, expected_channel, expected_chat, text, source_kind, source_id)
    if previous:
        return previous
    if os.getenv('YOUTUBE_CHAT_ENABLED', 'false').lower() != 'true':
        raise ValueError('Sending is disabled; set YOUTUBE_CHAT_ENABLED=true explicitly')
    target = context(project, stream)
    if target['channel']['id'] != expected_channel or target['chat_id'] != expected_chat:
        raise ValueError('Posting identity or live chat changed; preview again')
    access_token = auth.token(project)
    if os.getenv('YOUTUBE_CHAT_ENABLED', 'false').lower() != 'true':
        raise ValueError('Sending is disabled; set YOUTUBE_CHAT_ENABLED=true explicitly')
    delivery_id = str(uuid.uuid4())
    with quota.connect() as conn:
        inserted = conn.execute('INSERT INTO youtube_chat_deliveries '
            '(id,request_key,project_id,channel_id,video_id,chat_id,message_text,source_kind,source_id,status) '
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'sending') ON CONFLICT (request_key) DO NOTHING RETURNING id",
            (delivery_id,request_key,project,expected_channel,video,expected_chat,text,source_kind,source_id)).fetchone()
    if not inserted:
        return existing(request_key, project, video, expected_channel, expected_chat, text, source_kind, source_id)
    status, response_id, reason = 'unknown', None, None
    try:
        response = quota.tracked_request(project, 'liveChatMessages.insert', access_token,
            params={'part': 'snippet'}, account_channel_id=expected_channel, delivery_id=delivery_id,
            body={'snippet': {'liveChatId': expected_chat, 'type': 'textMessageEvent',
                              'textMessageDetails': {'messageText': text}}})
        response_id = response['id']
        status = 'sent'
    except (quota.QuotaExceeded, quota.ChatRateLimited, ValueError):
        status, reason = 'rejected', 'local_validation_or_budget'
    except quota.YouTubeRequestFailed as error:
        status = 'rejected' if error.definitive else 'unknown'
        reason = error.reason
    except Exception:
        # Database loss after a successful POST is also ambiguous. Never auto resend.
        reason = 'delivery_outcome_unknown'
    with quota.connect() as conn:
        conn.execute('UPDATE youtube_chat_deliveries SET status=%s,response_id=%s,error_reason=%s,completed_at=now() WHERE id=%s',
                     (status,response_id,reason,delivery_id))
    return {'id': delivery_id, 'status': status, 'response_id': response_id, 'error_reason': reason}


def recover():
    """Reconcile interrupted sends from the durable API ledger; never retry."""
    initialize()
    with quota.connect() as conn:
        rows = conn.execute("SELECT id FROM youtube_chat_deliveries WHERE status='sending' "
                            "AND created_at < now()-interval '5 minutes' FOR UPDATE").fetchall()
        for (delivery_id,) in rows:
            attempt = conn.execute('SELECT status,response_id FROM youtube_api_requests '
                                   'WHERE delivery_id=%s ORDER BY requested_at DESC LIMIT 1', (delivery_id,)).fetchone()
            status = 'sent' if attempt and attempt[0] == 'succeeded' and attempt[1] else 'unknown'
            conn.execute('UPDATE youtube_chat_deliveries SET status=%s,response_id=%s,error_reason=%s,completed_at=now() WHERE id=%s',
                         (status,attempt[1] if attempt else None,'recovered_after_interruption',delivery_id))
    return {'reconciled': len(rows)}


def current_message(stream):
    from utilities.current_person_message import generate_current_message
    return validate_message(generate_current_message(stream))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('connect','disconnect','status','preview','send',
                                          'preview-current','send-current','deliveries','recover'))
    parser.add_argument('--message-file', type=Path)
    parser.add_argument('--request-key')
    parser.add_argument('--expected-channel')
    parser.add_argument('--expected-chat')
    args = parser.parse_args()
    project = os.getenv('YOUTUBE_PROJECT_ID', '')
    stream = os.getenv('STREAM_URL', 'https://www.youtube.com/watch?v=vaiHWiExDXg')
    try:
        with auth.locked():
            if args.command == 'deliveries':
                result = deliveries()
            elif args.command == 'recover':
                result = recover()
            elif args.command == 'disconnect':
                result = auth.disconnect()
            else:
                if not project:
                    raise ValueError('Set YOUTUBE_PROJECT_ID in .env first')
                if args.command == 'connect':
                    # Persist explicit budget before the first authenticated identity read.
                    quota.configure(project, int(os.getenv('YOUTUBE_DAILY_QUOTA_UNITS', '10000')))
                    auth.authorize(project)
                    result = {'connected': True, 'channel': identify(project, auth.token(project))}
                elif args.command == 'status':
                    try:
                        result = {'connected': True, 'target': context(project, stream)}
                    except auth.AuthorizationRequired as error:
                        result = {'connected': False, 'authorization_required': str(error)}
                    result['quota'] = quota.report(project)
                else:
                    if args.command.endswith('-current'):
                        text = current_message(stream)
                    else:
                        if not args.message_file:
                            raise ValueError('--message-file is required')
                        text = validate_message(args.message_file.read_text().rstrip('\r\n'))
                    if args.command in ('preview','preview-current'):
                        result = {'target': context(project, stream), 'message_text': text, 'characters': len(text), 'publishes': False}
                    else:
                        result = send(project, stream, text, args.request_key, args.expected_channel, args.expected_chat,
                                      source_kind='current_person_message' if args.command=='send-current' else 'manual')
                        result['message_text'] = text
            print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    except Exception as error:
        # Print only safe errors we construct, never provider exceptions with secrets.
        safe_types = (ValueError, auth.AuthorizationRequired, quota.QuotaExceeded, quota.ChatRateLimited, quota.YouTubeRequestFailed)
        message = str(error) if isinstance(error, safe_types) else 'Operation failed; check configuration, credentials and database availability'
        print(json.dumps({'error': message}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
