"""Idempotently preserve the investigated incident; performs no Google requests."""
from datetime import datetime,timezone
import json
from zoneinfo import ZoneInfo
from psycopg.types.json import Jsonb
import youtube_chat as chat
import youtube_quota as quota

chat.initialize()
with quota.connect() as conn:
    row=conn.execute('SELECT r.id,r.project_id,r.account_channel_id,r.requested_at,r.http_status,r.error_reason,'
                     'e.received_at,d.message_text,d.id,e.status FROM youtube_bot_command_events e '
                     'JOIN youtube_chat_deliveries d ON d.id=e.delivery_id '
                     'JOIN youtube_api_requests r ON r.delivery_id=d.id WHERE e.id=23').fetchone()
    assert row and row[4:6]==(403,'rateLimitExceeded'),row
    request,project,channel,attempt,http_status,reason,received,text,delivery,event_status=row
    previous=conn.execute("SELECT id,requested_at FROM youtube_api_requests WHERE project_id=%s "
                          "AND account_channel_id=%s AND method='liveChatMessages.insert' AND status='succeeded' "
                          'AND requested_at<%s ORDER BY requested_at DESC LIMIT 1',(project,channel,attempt)).fetchone()
    details={'event_id':23,'command':'!ile','delivery_id':str(delivery),'request_id':str(request),
        'project_id':project,'channel_id':channel,'http_status':http_status,'reason':reason,
        'received_at':received.isoformat(),'attempted_at':attempt.isoformat(),
        'attempted_at_warsaw':attempt.astimezone(ZoneInfo('Europe/Warsaw')).isoformat(),
        'prepared_reply':text,'event_status':event_status,
        'previous_success_request_id':str(previous[0]),'previous_success_at':previous[1].isoformat(),
        'seconds_since_previous_success':(attempt-previous[1]).total_seconds(),
        'investigation_snapshot':{'historical_not_current':True,'api_day':'2026-10-25',
            'attempts':128,'estimated_units':322,'local_daily_budget':10000,
            'prior_30_minutes':{'posting_attempts':2,'successful_posts':1,'rejected_posts':1,
                               'stream_openings':39,'channels_list':44,'videos_list':43,
                               'channels_list_accessNotConfigured':1}},
        'interpretation':{'provider_meaning':'Account posting frequency restriction',
            'exact_provider_trigger':'unknown','daily_quota_exhaustion_supported':False,
            'viewer_11_messages_per_30_seconds_is_not_guaranteed_api_allowance':True,
            'original_provider_message_domain_retry_after':'not retained; cannot reconstruct',
            'reader_reconnect_churn':'Observed separately; persistent stream and reconnect backoff recommended, not proven cause'},
        'policy':quota.rate_limits.POLICY,'resend_this_reply':False}
    conn.execute('INSERT INTO youtube_operational_incidents(incident_key,request_id,details) VALUES (%s,%s,%s) '
                 'ON CONFLICT(incident_key) DO UPDATE SET details=EXCLUDED.details',
                 ('youtube-rate-limit-2026-10-26-command-23',request,Jsonb(details)))
    conn.execute('INSERT INTO youtube_api_error_details(request_id,http_status,details) VALUES (%s,%s,%s) '
                 'ON CONFLICT DO NOTHING',(request,403,Jsonb({'reason':reason,'historical_diagnostics_unavailable':True})))
    restored=quota.rate_limits.restore_history(conn,channel)
    state=conn.execute('SELECT not_before,consecutive_rejections FROM youtube_account_posting_state WHERE channel_id=%s',(channel,)).fetchone()
print(json.dumps({'stored':True,'incident_key':'youtube-rate-limit-2026-10-26-command-23','details':details,
                  'restored_history_rows':restored,'account_state':state,'real_google_requests':0},default=str,ensure_ascii=False,indent=2))
