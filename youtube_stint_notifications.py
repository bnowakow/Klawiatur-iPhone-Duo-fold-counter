"""One durable automatic chat summary for each newly completed folding stint."""
from datetime import datetime, timedelta, timezone
import json
import threading
import youtube_auth as auth
import youtube_chat as chat
import youtube_quota as quota

END_REASONS = ('sustained_clothing_change', 'night_break_sign_counter_stopped_review_required',
               'night_pause_counter_stopped_review_required')
SCHEMA = """
CREATE TABLE IF NOT EXISTS youtube_stint_notification_settings (
 stream_url text PRIMARY KEY, enabled boolean NOT NULL DEFAULT true,
 enabled_at timestamptz NOT NULL DEFAULT now(), max_age_seconds integer NOT NULL DEFAULT 120 CHECK(max_age_seconds>0)
);
CREATE TABLE IF NOT EXISTS youtube_stint_notifications (
 session_id uuid PRIMARY KEY, stream_url text NOT NULL, ended_at timestamptz NOT NULL,
 closure_confirmed_at timestamptz NOT NULL, end_reason text NOT NULL,
 folds bigint, pace numeric, reply_text text, request_key text NOT NULL UNIQUE,
 status text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
 delivery_id uuid, error_reason text
);
"""


def initialize(stream):
    with quota.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('youtube-stint-notifications-schema'))")
        conn.execute(SCHEMA)
        conn.execute('INSERT INTO youtube_stint_notification_settings(stream_url) VALUES (%s) ON CONFLICT DO NOTHING', (stream,))
        conn.execute('UPDATE youtube_stint_notification_settings SET max_age_seconds=LEAST(max_age_seconds,120) WHERE stream_url=%s',(stream,))


def finished_message(pace, folds, disclaimer):
    from utilities.current_person_message import message_for_pace
    if pace is None or folds is None:
        raise ValueError('Completed stint has insufficient measurements')
    text = message_for_pace(pace, folds, compact=True)
    text = text.replace('Złożenia aktualnej osoby:', 'Koniec zmiany. Złożenia:', 1)
    return chat.validate_message(text+' '+disclaimer)


def process_finished(project, stream, now=None):
    now = now or datetime.now(timezone.utc)
    with quota.connect() as conn:
        policy = conn.execute('SELECT enabled_at,max_age_seconds FROM youtube_stint_notification_settings '
                              'WHERE stream_url=%s AND enabled', (stream,)).fetchone()
        if not policy:
            return []
        disclaimer = conn.execute("SELECT disclaimer FROM youtube_bot_commands WHERE command='!ile'").fetchone()[0]
        rows = conn.execute('SELECT r.id,r.ended_at,COALESCE(e.confirmed_at,r.version_at),r.end_reason, '
                            'r.folds_since_detection_baseline,r.folding_folds_per_hour / 60 '
                            'FROM presenter_session_rates r LEFT JOIN presenter_session_ends e ON e.session_id=r.id '
                            'WHERE r.stream_url=%s AND r.ended_at>=%s AND COALESCE(e.confirmed_at,r.version_at)>=%s '
                            'AND r.end_reason=ANY(%s) AND NOT EXISTS '
                            '(SELECT 1 FROM youtube_stint_notifications n WHERE n.session_id=r.id) '
                            'ORDER BY COALESCE(e.confirmed_at,r.version_at) LIMIT 20',
                            (stream,policy[0],policy[0],list(END_REASONS))).fetchall()
    results = []
    for session_id,ended,confirmed,reason,folds,pace in rows:
        key = 'stint-finished:'+str(session_id)
        status, text, error = 'processing', None, None
        if not 0 <= (now-confirmed).total_seconds() <= min(policy[1],120):
            status, error = 'expired', 'closure_notification_too_old_or_future'
        else:
            try:
                text = finished_message(pace,folds,disclaimer)
            except ValueError:
                status, error = 'unavailable', 'insufficient_or_invalid_final_measurements'
        with quota.connect() as conn:
            # Recheck the persisted enable switch in the claiming transaction.
            enabled = conn.execute('SELECT enabled FROM youtube_stint_notification_settings WHERE stream_url=%s FOR SHARE', (stream,)).fetchone()
            if not enabled or not enabled[0]:
                break
            claimed = conn.execute('INSERT INTO youtube_stint_notifications '
                '(session_id,stream_url,ended_at,closure_confirmed_at,end_reason,folds,pace,reply_text,request_key,status,error_reason) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(session_id) DO NOTHING RETURNING session_id',
                (session_id,stream,ended,confirmed,reason,folds,pace,text,key,status,error)).fetchone()
        if not claimed:
            continue
        delivery_id = None
        if status == 'processing':
            try:
                with auth.locked():
                    target = chat.context(project,stream)
                    delivery = chat.send(project,stream,text,key,target['channel']['id'],target['chat_id'],
                                         source_kind='stint_finished',source_id=str(session_id),
                                         expires_at=confirmed+timedelta(seconds=min(policy[1],120)))
                status, delivery_id, error = delivery['status'],delivery['id'],delivery.get('error_reason')
            except Exception:
                status, error = 'unknown', 'automatic_summary_interrupted_or_unavailable'
        with quota.connect() as conn:
            conn.execute('UPDATE youtube_stint_notifications SET status=%s,delivery_id=%s,error_reason=%s,completed_at=now() '
                         'WHERE session_id=%s', (status,delivery_id,error,session_id))
        results.append({'event':'stint_finished_summary','session_id':str(session_id),'status':status,'delivery_id':delivery_id})
    return results


def recover():
    with quota.connect() as conn:
        conn.execute("UPDATE youtube_stint_notifications n SET status=CASE WHEN d.status='sending' THEN 'unknown' ELSE d.status END, "
                     "delivery_id=d.id,error_reason='recovered_after_interruption',completed_at=now() FROM youtube_chat_deliveries d "
                     "WHERE n.request_key=d.request_key AND n.status='processing' AND n.created_at<now()-interval '5 minutes'")
        conn.execute("UPDATE youtube_stint_notifications SET status='unknown',error_reason='interrupted_before_delivery',completed_at=now() "
                     "WHERE status='processing' AND created_at<now()-interval '5 minutes'")


def start(project,stream,stop):
    initialize(stream)
    def loop():
        while not stop.is_set():
            try:
                recover()
                for result in process_finished(project,stream):
                    print(json.dumps(result),flush=True)
            except Exception as error:
                # Never print provider errors or credentials.
                print(json.dumps({'event':'stint_notification_error','reason':type(error).__name__}),flush=True)
            stop.wait(15)
    thread = threading.Thread(target=loop,name='stint-notifications',daemon=True)
    thread.start()
    return thread
