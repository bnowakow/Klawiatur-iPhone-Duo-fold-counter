# Posting limits, diagnostics and stale replies

The app retains posting failures for analysis. It never queues a failed reply to
send after a cooldown. A later new command generates new measurements.

## Investigated incident: 26 October 2026

Command event **23** (`!Ile`, normalized `!ile`) arrived at **07:04:11.599
Europe/Warsaw**. Its reply POST at **07:04:11.980** failed with HTTP 403,
`rateLimitExceeded`. The prepared reply described 2,624 folds, 44.87 folds/minute
and a 2,692 folds/hour projection; those are historical measurements, not current
values. The preceding successful app POST was at **06:48:53.265**, about 15 minutes
19 seconds earlier. The exact YouTube trigger remains unknown.

The investigation's historical local quota snapshot recorded 128 attempts and
322 estimated units against a configured 10,000-unit budget for Pacific API day
2026-10-25. The prior 30-minute window contained two app POST attempts (one success,
one rejection), 39 stream openings, 44 channel lookups and 43 video lookups.
One channel lookup failed with `accessNotConfigured`. Test projects were excluded.
These are dated investigation observations, not live usage totals or proof of
Google's quota accounting.

YouTube describes this error as posting too frequently; it is distinct from a
daily quota failure. The viewer-facing 11 messages/30 seconds rule does not
establish a safe API posting threshold. See the official
[insert reference](https://developers.google.com/youtube/data/live/reference/rest/v3/liveChatMessages/insert).
Repeated reader connections merit separate investigation: retain a persistent
stream, resume its cursor and back off reconnects as described by the official
[streamList reference](https://developers.google.com/youtube/data/live/reference/rest/v3/liveChatMessages/streamList).
Reader churn is not established as the cause of the posting rejection; this
change does not repair the reader reconnect loop.

## Delivery policy

An account-wide pause persists across worker restarts and applies to commands,
manual posting and stint summaries, including multiple projects using the same
channel. Consecutive provider rejections set pauses of 10, 20, 40 and then 60
minutes, plus up to 5% positive jitter. A longer provider `Retry-After` wins.
A successful fresh POST resets the rejection streak. These delays are app policy,
not documented YouTube unblock guarantees. Startup restores missing state from
request history using original attempt completion times, without posting.

There is no delayed-send queue, probe or automatic POST retry. Commands received
during a pause are terminal `throttled` events. Direct sends and summaries blocked
by the pause are terminal `rejected` records. They remain in the database for
audit and cannot become sends when the timer expires. Summaries are recorded even
when suppressed by a pause.

All outgoing text has a deadline at most 120 seconds after preparation. Command
replies also expire at most 120 seconds after the incoming command's publication;
stint summaries expire at most 120 seconds after closure confirmation. Existing
summary age settings are clamped to 120 seconds. Expiry is checked before
identity discovery, after authentication, and before HTTP dispatch (including
after quota reservation). No new dispatch starts after expiry. An HTTP request
already in flight cannot be recalled. A reused request key always returns its
original terminal result. Unknown POST outcomes are retained without retry.

## Stored evidence

Existing `youtube_bot_command_events`, `youtube_chat_deliveries` and
`youtube_api_requests` retain incoming commands, prepared reply text, targets,
timestamps, request links, outcomes and error reasons. New tables retain:

| Table | Contents |
| --- | --- |
| `youtube_operational_incidents` | Investigated incident, historical usage snapshot, interpretation, unknowns and references |
| `youtube_api_error_details` | Sanitized provider message/domain/errors, HTTP status and raw/parsed Retry-After |
| `youtube_account_posting_state` | Channel pause deadline, rejection streak and last success/error times |
| `youtube_posting_backoff_events` | Per-request pause decision, policy and provider context |
| `youtube_posting_policies` | Seeded policy snapshot for audit; runtime policy lives in code |
| `youtube_delivery_expirations` | Deadline associated with a claimed or suppressed delivery |

Credentials are excluded and recognizable tokens redacted. Historical raw
provider text, domain and Retry-After for event 23 were not retained; the incident
explicitly records that absence rather than inventing diagnostics. Audit rows
have no automatic expiry. Database storage remains independent of the image cap.

```sql
SELECT details FROM youtube_operational_incidents
WHERE incident_key = 'youtube-rate-limit-2026-10-26-command-23';
SELECT * FROM youtube_account_posting_state;
SELECT * FROM youtube_posting_backoff_events ORDER BY observed_at DESC LIMIT 20;
```

`youtube_rate_limits_integration_check.py` and the notification integration check
use real PostgreSQL with simulated Google responses. They verify shared pauses,
provider diagnostics, expiry during processing, terminal suppression, fresh sends
and restart recovery. No real public messages are posted by these tests.

## Compose monitoring

`docker compose logs -f youtube-bot` shows immediately flushed stdout JSON when
a provider posting rate limit occurs (`youtube_posting_rate_limited`, `level`
`ERROR`). `retry_in_seconds` and `retry_in` (`HH:MM:SS`) show the wait at log time;
`not_before` and `not_before_warsaw` give the absolute deadline. Existing pauses
are reported on startup as `posting_cooldown_active` with the same time fields.
This is the earliest locally permitted fresh attempt, not a promise that YouTube
will accept it. There is no delayed resend of old replies.

## Web investigation and request audit — 26 October 2026

The API insert reference documents a posting-frequency rejection but publishes
no numeric API posting interval or guaranteed recovery duration. The viewer
help page states **11 messages per 30 seconds**, which is not a guarantee that
an API client/account will always be permitted that rate. Stream-specific slow
mode is a separate setting. Sources checked:

- [Insert request and errors](https://developers.google.com/youtube/data/live/reference/rest/v3/liveChatMessages/insert)
- [Viewer chat limits](https://support.google.com/youtube/answer/15270973?hl=en)
- [Slow mode and moderation](https://support.google.com/youtube/answer/9826490?hl=en)
- [Daily quota costs](https://developers.google.com/youtube/data/determine_quota_cost)

The real project's persisted POST history at investigation time was:

| Warsaw time | Result | Minutes since previous app POST attempt |
| --- | --- | ---: |
| 06:48:53 | 200 success | — |
| 07:04:11 | 403 rateLimitExceeded | 15.31 |
| 07:16:06 | 403 rateLimitExceeded | 11.91 |
| 07:55:05 | 403 rateLimitExceeded | 38.99 |
| 08:38:44 | 403 rateLimitExceeded | 43.64 |

No safe posting frequency can be established from one success followed by these
rejections. A delay of 44 minutes between attempts did not suffice on this account.
A persistent account restriction or API-side issue is a hypothesis, not a proven
cause. This app's ledger does not include activity by the same channel in other
apps or browser sessions. Local estimated daily totals were 2,116 units on Pacific
API day 2026-10-25 and 1,555 on 2026-10-26; Google Cloud remains authoritative.
The returned reason was not `quotaExceeded`. Public issue searches did not
establish a verified applicable numeric limit or the cause of this incident.

The running bot's actual send path was audited with fake authentication and
mocked HTTP/database writes, without any real Google request. It built exactly:

```http
POST https://www.googleapis.com/youtube/v3/liveChat/messages?part=snippet
Authorization: Bearer <OAuth access token>
Content-Type: application/json
```

```json
{"snippet":{"liveChatId":"<current chat ID>","type":"textMessageEvent","textMessageDetails":{"messageText":"<reply>"}}}
```

The stored OAuth scope is `https://www.googleapis.com/auth/youtube.force-ssl`,
which the insert reference accepts. Polish text survived JSON serialization.
Only one simulated HTTP call occurred; no retry was attempted. `textMessageEvent`
is the required request-body value; an outdated error-table entry saying `text`
should not override the method's explicit request-body schema.

The request format matches the documented contract. That does not prove the
current account will be allowed to post or identify the restriction's cause.
A useful next diagnostic, after the pause, is one manually sent message from
**the same authorized bot channel** in YouTube's own interface, comparing any
visible error with the API failure and actual Cloud Console limits. No manual
message or API test was sent during this investigation. Do not infer a guaranteed
unblock time from our local countdown, and never resend historical replies.

### User follow-up: manual chat works, API chat does not

On 26 October 2026 the user confirmed that manual messages come through, while
only the app's first API message succeeded. Treat this as user-reported evidence;
manual message IDs and channel identity were not independently inspected here.
This makes a general inability to participate in chat less likely and shifts the
investigation toward the API posting path, API-specific enforcement, and
project/client/account interactions. None is yet established as the cause.
Do not describe cooldown expiry as a fix or infer a safe interval from it.

The next useful controlled test is one fresh message through Google's official
API Explorer after the local pause, using the same channel and live chat. This
still publishes a message and must be explicitly authorized before execution.
A failure there would reproduce the problem outside our HTTP implementation; a
success would warrant comparing client/project identity, credentials, payload,
and request context. Explorer uses a different client/project context, so it is
not an otherwise-identical test and cannot alone prove an app bug. Do not rotate
accounts/projects or change identifiers to work around a restriction. No Explorer
request, reauthorization, quota change or public post was performed for this
follow-up. Targeted public searches did not identify a verified matching cause.

### Authorized independent-client test succeeded; production transport switched

At **09:08:28 Warsaw time on 26 October 2026**, an explicitly authorized single
fresh `!pomoc` reply was accepted (HTTP 200) using `requests.Session`, with the
same project, OAuth credentials, channel and chat. The user explicitly allowed
this diagnostic to bypass the local cooldown once. There were no POST retries.
A read-only chat lookup confirmed the exact text, author and publication time;
the user also confirmed visibility with a screenshot. This was **not API Explorer**.
The request/delivery and diagnostic incident are retained under
`authorized-independent-help-diagnostic-2026-10-26-01`.

The diagnostic initially tried to parse compressed `response.raw` bytes without
decompression, so it temporarily recorded an unknown outcome. It did not repost.
Readback confirmed the successful message and reconciled the records to `sent` /
`succeeded`. The diagnostic script now requests decompression explicitly. The
historic decoding issue is retained in the incident rather than erased.

At the user's request, production `liveChatMessages.insert` now uses the same
`requests.Session` / `HTTPAdapter(max_retries=0)` path in `youtube_http.py`.
Redirect following is disabled, connect/read timeouts are 10/30 seconds, and
streamed responses are explicitly decompressed before bounded JSON parsing.
Compressed error bodies retain provider details and `Retry-After`. Read-only HTTP
calls keep the prior transport. The shared accounting, deadline checks, persistent
account cooldown, idempotency and no-old-reply policy still apply. The one-off
override was not added to normal app sending.

The success establishes that this client could post at that time. It does **not**
prove `urllib` caused the earlier failures: elapsed time and other conditions also
changed. Tests exercise actual Requests POSTs against a localhost server,
including compressed Polish JSON, rate-limit errors, redirects, server failures,
size limits and no retries; integration tests simulate Google with real PostgreSQL.
No extra public post is needed merely to deploy this change.

## Future consideration: alternate sender and per-attempt transport audit

The user proposed trying a previous HTTP client when an API send fails, and
recording which method sent each request and its outcome. On 26 October 2026
they asked to document this for future consideration. **This is a proposal, not
an enabled fallback policy or an implemented per-request transport audit.**

Current evidence does not establish a transport-only cause: the independent
Requests diagnostic sent `!pomoc` successfully at 09:08:28 Warsaw time, whereas
the production Requests sender's fresh `!ile` reply at 09:18:19 was rejected with
403 `rateLimitExceeded`. Timing, message text and request context differed.
Changing the client alone has not established reliable posting. Message-specific
enforcement is another hypothesis, not a demonstrated explanation.

An automatic alternate-client POST is generally unsuitable after these outcomes:

- `rateLimitExceeded`: preserve the account pause and honor Retry-After. Changing
  HTTP clients does not establish that another attempt is permitted.
- Timeout, connection loss after dispatch, 5xx, or malformed/misdecoded success
  response: delivery may already have happened. Reconcile using durable evidence
  or read-only chat lookup; do not send another copy. The diagnostic's HTTP 200
  compressed-response issue demonstrates why an unreadable body is not proof of
  a failed delivery.
- Permanent authorization, target, validation or chat-state errors: address the
  underlying error rather than trying the same action through another client.

A possible future fallback could be considered only when the app can establish
that the first attempt never dispatched. It would require its own explicit
policy, bounded attempts, current expiry check and normal account/quota guards.
A separate manually authorized diagnostic comparison can be useful, but it must
be identified as a diagnostic exception rather than an automatic recovery path.
No old or expired reply should be queued for later delivery.

If implemented, retain an immutable record **per attempt**, linked to one logical
delivery, including request ID, API method, selected HTTP transport/client version,
primary versus fallback, parent attempt/reason, preparation/expiry/dispatch/
completion timestamps, HTTP status, sanitized provider reason/message/Retry-After,
returned message ID and outcome (`not_dispatched`, `succeeded`, `rejected`,
`unknown`, `expired`, or `blocked`). Record whether a delivery was later confirmed
by readback and retain its original uncertain outcome as history. Account for
quota per actual/reserved attempt; never log OAuth tokens or credentials.

Existing `youtube_api_requests` and `youtube_chat_deliveries` already link API
methods, replies, timestamps and outcomes. Diagnostic incidents identify their
client explicitly. General per-attempt transport/version/fallback metadata would
be an additional future improvement; it should not be inferred as a historical
fact when the client was not recorded. Future comparisons should control the
channel/chat, message content, cooldown and timing as far as practical, and
recognize that changing Google clients/projects introduces additional variables.
