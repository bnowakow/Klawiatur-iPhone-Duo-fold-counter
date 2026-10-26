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
