"""Print the latest persisted posting rate-limit error, without making API calls.

Executed on stdin in the bot container by make docker-logs-errors, so container
rebuilds are unnecessary when changing this display utility.
"""
import json
import math
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import youtube_quota as quota

with quota.connect() as conn:
    row=conn.execute("SELECT r.id,COALESCE(r.completed_at,r.requested_at),r.error_reason,"
                     "s.not_before,s.not_before>now(),s.last_success_at "
                     "FROM youtube_api_requests r LEFT JOIN youtube_account_posting_state s "
                     "ON s.channel_id=r.account_channel_id WHERE r.project_id=%s "
                     "AND r.method='liveChatMessages.insert' AND r.error_reason='rateLimitExceeded' "
                     "ORDER BY r.requested_at DESC LIMIT 1",(os.environ['YOUTUBE_PROJECT_ID'],)).fetchone()
if row:
    def local(stamp):
        return stamp.astimezone(ZoneInfo('Europe/Warsaw')).isoformat() if stamp else None
    remaining=max(0,math.ceil((row[3]-datetime.now(timezone.utc)).total_seconds())) if row[3] else 0
    hours,remainder=divmod(remaining,3600)
    minutes,seconds=divmod(remainder,60)
    print(json.dumps({'event':'persisted_youtube_posting_error','historical':True,
                      'request_id':str(row[0]),'reason':row[2],
                      'error_at_warsaw':local(row[1]),'posting_not_before_warsaw':local(row[3]),
                      'local_pause_active':bool(row[4]),'last_success_at_warsaw':local(row[5]),
                      'retry_in_seconds':remaining,'retry_in':f'{hours:02d}:{minutes:02d}:{seconds:02d}',
                      'note':'Stored error history; pause expiry does not confirm YouTube recovery'},ensure_ascii=False),flush=True)
