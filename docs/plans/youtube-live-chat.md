# Plan: Google OAuth and YouTube live-chat posting

Status: OAuth, manual delivery and command extension interfaces implemented on
2026-10-26; real account authorization and real posting await credentials and user
chosen content. Incoming-message reading, bot cooldowns and scheduling remain future
work. See [current setup](../youtube-setup.md). This plan does not enable posting.

## Outcome and scope

Authorize one personal Google/YouTube account, identify its posting channel, and
publish messages to the active chat for configured `STREAM_URL` (initially video
`vaiHWiExDXg`). Persist delivery outcomes and account for every official YouTube
API attempt using the existing PostgreSQL quota ledger.

Deliver manual posting first. Scheduled folding updates are a second phase with
an explicit enable switch, chosen message template and schedule. Keep acquisition
and counter analysis independent so OAuth or YouTube failures do not stop monitoring.
No public dashboard or multiple-account management is needed for this version.

## Existing foundation

- `youtube_quota.py`: policy snapshots, configured project budgets, conservative
  reservations, authenticated HTTP transport and daily reports.
- PostgreSQL and Docker Compose; private host `.env` configuration.
- Supervisor observations, confirmed presenter sessions, pace and night-break data.
- No OAuth browser flow, credentials, live-chat discovery or posting queue yet.

## 1. Configuration and account authorization

Add `youtube_auth.py` using Google's maintained OAuth and credential libraries.
Use a Google Cloud **Web application** OAuth client and a registered local callback,
for example `http://localhost:8765/oauth/callback`. The user creates the Cloud
project, enables YouTube Data API v3, downloads the client configuration and adds
their account as a test user when the consent screen is in Testing.

Provide a temporary Compose service/profile for browser authorization. Publish
its callback port only on host `127.0.0.1`; inside Docker bind the listener to the
container interface. Print a Google authorization URL for the host browser.
Request offline access and `youtube.force-ssl`, which is accepted by the insert
endpoint; explain its broad YouTube permission in the setup instructions.

Use a random, expiring, single-use OAuth state tied to the initiating authorization
session; reject mismatched/replayed callbacks. Enable PKCE where supported by the
chosen library flow. Fix the redirect URI to configuration, validate callback
Host/path and stop the listener after success or a short timeout. Redact query
strings and authorization codes from HTTP/access logs. Reconnecting must not
silently replace an existing posting identity.

Store the client JSON and refresh credentials in a dedicated ignored host folder,
for example `./secrets/youtube/`, with directory mode 0700 and files 0600. Mount it
only into authorization and chat containers. Keep `.env` for paths and nonsecret
settings; avoid putting tokens in command arguments, images, database rows or logs.
Use atomic credential writes and serialize refreshes across processes.

After consent, call `channels.list(mine=true, part=snippet)` through quota accounting.
Save the returned channel ID/title and show the identity in status and preview.
Handle an account without a usable YouTube channel and require a channel selection
if the API returns multiple candidates. Refresh access tokens when needed. Preserve
an existing refresh token if a refresh response does not include a replacement.
Expose `reauthorization_required` on revocation, expiry or `invalid_grant`.

Provide disconnect/revoke and local credential deletion. A failure to revoke
remotely must be visible; local disconnect still blocks sends immediately.
External Testing authorization can expire after seven days; explain the path to
Production and any applicable verification in documentation.

Acceptance: authorize in the host browser, identify the intended YouTube channel,
restart Docker and recover access, then revoke and verify sends are blocked.

## 2. Chat discovery and safe manual sending

Add `youtube_chat.py`, sharing auth, configuration and quota helpers.

Proposed CLI, available through Compose/Make targets:

- `auth connect`, `auth status`, `auth disconnect`.
- `chat status`: channel identity, configured video, active chat and quota summary.
- `chat preview --text ...`: validate text and show exact identity/target/content;
  local preview does not publish. Explicit target resolution can consume read quota.
- `chat send --message-file PATH --request-key KEY`: publish one explicitly requested
  message and return its durable delivery status and YouTube message ID.
- `chat deliveries`: inspect recent attempts and unresolved outcomes.

Resolve `liveStreamingDetails.activeLiveChatId` with `videos.list` for the configured
video ID. Cache the association with a short configurable TTL (proposed five minutes)
and bind it to the video ID. Refresh on a stale/missing-chat response for future
attempts. Never silently switch to a different video. An ended stream, disabled
chat, missing credentials or unknown posting identity blocks sending with a clear
reason. Channel slow mode, membership/subscriber restrictions and bans remain
subject to Google's response.

Validate nonblank text and the existing 200-character limit before reserving quota.
Construct `textMessageEvent` with `part=snippet` and send through `tracked_request()`.
All discovery/identity reads and each actual POST attempt must be recorded. OAuth
refresh traffic is separate from YouTube Data API quota. Require a configured
Cloud project budget before the first identity lookup.

Acceptance: preview shows the correct identity/chat/text; after explicit user
instruction, one manual message appears in the chat, its ID is saved, and the
20-unit POST estimate plus actual read attempts appear in quota reports.

## 3. Durable delivery and failure handling

Add an additive migration for `youtube_chat_deliveries` with: UUID, unique caller
request key, project/channel/video/chat IDs, exact message text, creation/expiry
and completion times, source observation/session references where relevant,
status, safe error detail and returned YouTube message ID. Exact text belongs in
this delivery audit, not the quota ledger. Document its retention and deletion.

Add a nullable indexed delivery reference to `youtube_api_requests`, allowing
multiple request attempts to remain attributable to one delivery. Refactor the
transport to attach that reference when it commits its pre-send reservation.
Claim a queued delivery transactionally, persist `sending` before external I/O,
and prevent two workers or repeated CLI invocations using the same request key
from sending it twice. Do not hold a database transaction during network I/O.

States: `queued -> sending -> sent | rejected | unknown`; queued messages can also
become `expired` or `cancelled`. A crash with a committed reservation and no terminal
result becomes `unknown` during recovery. Google does not provide a caller
idempotency key for this insert: local deduplication cannot guarantee exactly-once
publication across a crash or timeout. Keep uncertain outcomes visible and do not
resend them automatically, including when a successful response cannot be saved.

Retry read requests with bounded backoff and a fresh quota reservation each time.
Refresh credentials before posting where possible. A received 401 rejection may
allow one refresh/retry, with both attempts accounted; never retry an ambiguous
POST. Surface quota exhaustion, rate limiting, forbidden access and disabled/ended
chat distinctly. Pause automatic sends on authorization and access failures.

Acceptance: concurrent send commands produce one POST; restart recovery preserves
unknown outcomes; database/network failures never silently duplicate a message.

## 4. Optional scheduled folding updates

Add a separate `youtube-chat` Compose worker. Start disabled, with configurable
interval, active hours and a daily posting budget within the shared project budget.
Proposed starting point: one update every ten minutes during active folding, only
when the accepted counter advances. This is a proposal, not an enabled schedule.
At ten-minute spacing, a 24-hour day would use at most 2,880 estimated POST units
plus discovery/read traffic. Shorter active hours reduce that upper bound.

Generate a deterministic Polish template from accepted, recent counter readings
and established pace calculations. Include the observation timestamp and omit
unknown/low-confidence values; never use rejected OCR. Reference presenter stints
by their existing anonymous labels. Proposed data freshness limit: 90 seconds,
configurable to match acquisition/backpressure behavior.

Use one unique delivery key per stream and schedule slot, expire unsent updates
at the next slot, and skip missed slots after downtime. Do not burst a backlog.
Use the existing confirmed night-break state to suppress ordinary pace messages;
optional start/stop notifications need separate enable switches and session keys.
Changing accounts, stream targets or templates must invalidate inappropriate
queued messages. Add a kill switch checked immediately before every POST.

Before activation, choose message language/content, interval, active hours, daily
cap and whether night-break notifications are wanted. Defaults remain disabled
until these choices and the connected channel have been verified.

## 5. Measurement, deployment and verification

Extend status with connected channel, token health, active chat, local Pacific-day
quota used/remaining, delivery counts, last success and unresolved outcomes.
Continue labeling quota as a conservative local estimate; Google Cloud Console
is authoritative and other apps' usage is invisible. Keep test projects separate.
A configured reserve for reads prevents scheduled posts consuming the whole budget.

Add client/credential fixtures with fake values, mocked Google HTTP tests and
PostgreSQL integration tests. Cover OAuth state replay, token refresh/revocation,
secret redaction, chat discovery/cache invalidation, concurrent sends, failed and
unknown requests, quota reset/DST, local posting guards, stale readings, night
breaks, schedule slots and restart recovery. Automated tests must not publish real
messages. The final end-to-end check needs user authorization for specific text.

Update Dockerfile, Compose, `.env.sample`, `.gitignore`, Makefile `docker-upgrade`,
README, CONTEXT and the feature/storage docs. Include `make youtube-auth`,
`make youtube-status` and a clear one-message send command. Deploy additive database
changes without affecting folding history. Stopping the chat worker must leave the
supervisor, observations and existing quota/delivery history intact.

Suggested implementation commits:

1. OAuth credential handling, local authorization command and identity status.
2. Delivery schema, chat discovery and manual preview/send with quota linkage.
3. Failure recovery, integration tests, deployment and documentation.
4. Optional scheduled messages after manual posting is verified and settings chosen.

## Primary references

- [Google web-server OAuth flow](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Live-chat insert and required scopes](https://developers.google.com/youtube/v3/live/docs/liveChatMessages/insert)
- [Video lookup](https://developers.google.com/youtube/v3/docs/videos/list)
- [Quota snapshot and existing implementation](../youtube-live-chat.md)
