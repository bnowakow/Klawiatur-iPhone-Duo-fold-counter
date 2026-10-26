# Continuous !ile live-chat bot

The `youtube-bot` Compose service keeps an authenticated official YouTube gRPC
`liveChatMessages.streamList` connection open for the configured `STREAM_URL`.
It runs independently of Codex and the folding supervisor. Docker must remain
running with network access. It reconnects with a persisted page token, refreshes
OAuth credentials, and uses the existing local quota accounting for every stream
connection, discovery read and reply POST. No short-interval polling is used.

## Command and reply

The default row in `youtube_bot_commands` is:

| Field | Value |
| --- | --- |
| command | `!ile` |
| handler_key | `current_person` |
| enabled | true |
| cooldown_seconds | 600 |
| max_message_age_seconds | 120 |
| disclaimer | zapytanie !ile może być użyte nie częściej niż raz na 10 minut |

Matching is case-insensitive after trimming whitespace; the message must contain
only the command. Other text, arguments and non-text events do not trigger a reply.
The account owner may also issue the command. Bot replies contain a sentence with
`!ile` but are never themselves exact commands, so they cannot create a reply loop.
Additional commands can map to registered Python handler keys through database
rows; database values are never executed as code.

Replies use the same measurements and rounding as `make docker-message`, in a
shorter wording that fits YouTube's 200-character limit with the exact disclaimer:

> Złożenia aktualnej osoby: 2014. Tempo: 44,68 złożeń/min; prognoza: 2681 złożeń/h. zapytanie !ile może być użyte nie częściej niż raz na 10 minut

Numbers above are an example; actual responses use fresh, accepted observations.
The compact wording preserves the current-person fold count, average folds/minute
and hourly projection. If measurements are stale, attribution is uncertain or a
night break is detected, record `unavailable` and send nothing rather than invent
statistics. Oversized content is also rejected locally without truncation.

## Global cooldown and replay behavior

There is one cooldown per **chat and command**, shared across all viewers. A
transactional claim and advisory lock in `youtube_bot_cooldowns` admit only one
reply at a time. All attempts within the next 600 seconds are recorded as
`throttled` and do not generate a warning/reply. The cooldown is reserved before
posting, then extended to at least 600 seconds after send completion; an uncertain
or rejected POST keeps it conservatively. Unavailable measurements do not reserve
a cooldown. A changed policy is read from PostgreSQL for each command.

`youtube_bot_command_events` deduplicates `(chat_id,message_id)` and records the
command, source author ID, publication/receipt times, outcome, exact reply text,
request key and delivery reference. Resuming/reconnecting never repeats a claimed
command, including when its delivery outcome is unknown. An interrupted claim is
reconciled against delivery history; it is never automatically resent.

The worker ignores initial history older than its persisted activation time.
Commands older than 120 seconds after downtime are counted as stale and receive
no reply. Cursor advancement follows durable command processing. A changed chat
or posting identity resets the cursor/activation window. A PostgreSQL session
advisory lock permits one worker per configured stream. API failures use bounded
backoff, and loss of the ownership connection exits the worker for Docker to
restart it. A network partition during a send remains subject to the existing
unknown-delivery rules.

## Statistics and operations

```sh
make youtube-bot-up
make youtube-bot-stats
make youtube-bot-logs
make youtube-bot-stop
```

Stats show the current stream's command configuration, cooldowns, reader health,
all-time usage and recent daily aggregates. Integration fixture streams are
excluded when `STREAM_URL` is configured.

Database objects:

- `youtube_bot_commands`: persisted command handler, enable switch, cooldown,
  disclaimer and maximum accepted age; seeded once without overwriting changes.
- `youtube_bot_state`: activation time, stream/chat/channel, resume cursor,
  last batch, heartbeat and connection/error status.
- `youtube_bot_command_events`: one retained event per recognized command message.
- `youtube_bot_cooldowns`: latest reservation and next permissible reply time.
- `youtube_bot_command_usage`: received, unique users, answered, throttled, stale,
  unavailable, disabled, rejected, unknown and processing totals per chat/command.
- `youtube_bot_command_daily_usage`: daily totals using Europe/Warsaw dates.

```sql
SELECT * FROM youtube_bot_command_usage
WHERE stream_url='https://www.youtube.com/watch?v=vaiHWiExDXg';
SELECT * FROM youtube_bot_command_daily_usage
WHERE stream_url='https://www.youtube.com/watch?v=vaiHWiExDXg'
ORDER BY usage_day DESC;
SELECT command,cooldown_seconds,enabled FROM youtube_bot_commands;
```

Disable only a command without stopping the reader:

```sql
UPDATE youtube_bot_commands SET enabled=false,updated_at=now() WHERE command='!ile';
```

If changing the cooldown, also update the public disclaimer to match it. Existing
cooldown reservations are kept. Data has no automatic expiry; author IDs and reply
text remain in PostgreSQL until explicitly removed. This is a command audit, not
an archive of all chat messages. Duplicate deliveries are not additional usages.

The bot service explicitly enables sending for this authorized use; one-shot
manual chat containers still inherit `YOUTUBE_CHAT_ENABLED=false` from `.env`.
Use `make youtube-bot-stop` or the command's database enable switch to stop replies.
Stopping preserves usage, cooldown and cursor history. Docker's `unless-stopped`
policy restarts an enabled worker after failures/reboots. `make docker-upgrade`
rebuilds the bot and recreates it if it is already running; it does not activate
a stopped bot. The bot publishes no host ports and mounts only its credentials.

Every streaming connection costs an estimated five units; planned 15-minute
reconnects avoid access-token expiry, alongside backoff for failures. Each reply
costs an estimated 20 units plus identity/video reads. The shared project budget
still caps usage. OAuth External Testing grants may need weekly reauthorization;
health/error state exposes authorization failures. Streaming deadlines and API
availability mean reconnections can cause brief read interruptions.

## Verification

```sh
docker compose --profile youtube run --rm --no-deps youtube-chat python -m unittest discover -s proof-of-concept -p 'test_youtube_*.py' -v
docker compose --profile youtube run --rm --no-deps youtube-chat python youtube_bot_integration_check.py
```

The integration check uses isolated fixture chat/stream IDs, simulated sends and
real PostgreSQL. It verifies usage counts, global cooldown across users, exact
boundaries, concurrent requests, uncertain outcomes, replay, stale history,
unavailable data and cursor rotation. It sends no real Google messages and saves
JSON evidence under `data/test-evidence/youtube-bot/`.

Transport is based on Google's [streaming guide](https://developers.google.com/youtube/data/live/streaming-live-chat)
and [streamList reference](https://developers.google.com/youtube/v3/live/docs/liveChatMessages/streamList).
`youtube_stream.proto` is a minimal wire-compatible subset of the official sample;
generated Python bindings are built inside Docker, not committed. See also
[OAuth/manual posting](youtube-setup.md) and [quota accounting](youtube-live-chat.md).

## Automatic end-of-stint summaries

The running bot also checks PostgreSQL every 15 seconds for newly completed
folding stints, independently of incoming chat traffic. A recognized sustained
handover or detected night stop produces one compact final-statistics message
with the same `!ile` disclaimer. It says `Koniec zmiany. Złożenia: …` and uses
that completed stint's stored fold count and average pace, even when another
person has already started. It never reads the successor's current statistics
for the previous person's summary.

`youtube_stint_notification_settings` stores the per-stream enable switch, first
activation time and maximum closure age (at most 120 seconds). Historical stints
ending before activation, open stints and manual baseline resets are excluded.
The system's existing inferred closure is the trigger; night stop confirmation
can therefore delay the message after the estimated stop time. Invalid/missing
final measurements produce a retained `unavailable` event, not invented numbers.

`youtube_stint_notifications` stores the final fold count/pace, closure reason and
confirmation time, exact text, unique session request key, delivery reference and
outcome. One notification is claimed per session UUID. Restarts, concurrent checks
and later corrections do not send the same session twice. Expired, rejected and
uncertain notifications remain visible; ambiguous sends are never automatically
retried. Network/configuration failures after a claim can leave an unknown outcome
requiring review rather than risking a duplicate public message.

Automatic finish summaries are independent of the chat-wide `!ile` request
cooldown: they are not delayed by a recent command and do not count as viewer
command usage. They still pass through the shared API budget, posting guard and
persistent delivery ledger, using `source_kind=stint_finished`. The command's
existing disclaimer is reused. Sending a summary costs the usual estimated
20 POST units plus identity/chat discovery reads.

`make youtube-bot-stats` also shows notification settings and the latest summaries.
Disable only automatic finish summaries while retaining command replies:

```sql
UPDATE youtube_stint_notification_settings SET enabled=false
WHERE stream_url='https://www.youtube.com/watch?v=vaiHWiExDXg';
```

The timer runs inside the same bot container; `make youtube-bot-stop` stops both
command replies and finish summaries. PostgreSQL history is retained. Integration
coverage sends no real messages:

```sh
docker compose --profile youtube run --rm --no-deps youtube-chat python youtube_stint_notifications_integration_check.py
```

Evidence is saved under `data/test-evidence/youtube-stint-notifications/`.

## !pomoc command

`!pomoc` replies with the list of enabled commands whose handlers are available,
including their descriptions from `youtube_bot_commands.description`. Currently:

> Komendy: !ile — liczba złożeń i tempo; !link — adres strony testu; !pomoc — lista komend; !samsung — wynik testu Samsunga. zapytanie !pomoc może być użyte nie częściej niż raz na 10 minut

Its own persisted policy uses `handler_key=help` and `cooldown_seconds=600`.
The cap is global across viewers for this command, independent of the `!ile`
cooldown. Repeated help requests during that window are recorded as throttled and
receive no reply. It uses the same deduplication, usage statistics, quota tracking
and durable delivery path as other commands. Help works without folding data;
disabled/unimplemented commands are excluded. Very long descriptions fall back
to the complete command-name list to fit the 200-character limit. Seeding preserves
operator policy changes and custom nonempty descriptions across restarts.

## !jak-długo stream duration

`!jak-długo` replies in Polish, for example: `Czas trwania transmisji: 1 dzień,
21 godzin, 4 minuty.`, followed by its cooldown disclaimer. It measures elapsed
UTC time from YouTube API `liveStreamingDetails.actualStartTime`, including
folding/night pauses and handling Warsaw daylight-saving changes correctly.
It does not use the scheduled start or the bot's uptime. Days, hours and whole
minutes use Polish plural forms. Streams shorter than one minute say
`poniżej minuty`; missing actual-start data is reported explicitly.

The existing API discovery read stores `actual_started_at`/`actual_ended_at` in
`youtube_bot_state` on startup/reconnection, so replies require no additional API
request. If an actual endpoint is available, the duration stops at that endpoint
and is described as a finished transmission. The command uses the standard
600-second shared-chat cooldown, 120-second message-age limit, deduplication and
posting protections, and appears in `!pomoc`.

## !kod-zródłowy source repository

`!kod-zródłowy` returns the project's GitHub repository:
`https://github.com/bnowakow/Klawiatur-iPhone-Duo-fold-counter`, followed by its
Polish cooldown disclaimer. The command spelling matches the requested
`!kod-zródłowy`. It uses the same 600-second shared-chat command cooldown,
120-second message-age limit, deduplication and account-wide posting protections.
The reply uses a static URL and does not fetch GitHub or folding measurements.
`!pomoc` includes it in the available-command list.

## !rekord completed-stint record

`!rekord` reports the largest accepted counter increase (`last_counter -
baseline_counter`) among ended stints with folding-start evidence for the
configured stream. Public replies include only the fold count and cooldown disclaimer.
Active stints, other streams and technical `manual_baseline_reset` closures are
excluded. Ties prefer the earliest endpoint, then UUID. It reports the best
recorded stint, not a verified all-time result or a named person's aggregate.

The handler does not read appearance descriptions. They are restricted to internal
use, PostgreSQL and Google Sheets. Public message validation and the API transport
reject structured appearance fields (clothing, hair, glasses, facial hair, headwear
and accessories), including labels with invisible formatting characters. This
extra check protects against accidental reuse of internal description text by
other send paths. If no eligible completed stint exists, the bot says so. It uses the same 600-second
shared-chat command cooldown, 120-second age limit, account posting protections
and delivery history. `!pomoc` includes it automatically.

## !link static website address

`!link` replies with exactly `ipduotest kropka pl`. It uses the standard delivery,
deduplication, usage statistics and account-wide posting protections, plus its
own shared-chat 600-second cooldown and 120-second message-age limit, matching
the other commands. It needs no live folding measurements or website request.
The static reply has no appended disclaimer. `!pomoc` includes the command;
help falls back to command names if the complete description list exceeds
YouTube's message-length limit.

## !samsung cached historical result

`!samsung` returns a Polish summary of the earlier Samsung Galaxy Z Flip3 test:
418,500 folds; the phone still worked but could not close fully. The source is
[the supplied Cyfrowa.rp.pl article](https://cyfrowa.rp.pl/smartfony/art36496851-ile-faktycznie-wytrzyma-smartfon-polski-youtuber-postanowil-sprawdzic),
checked on 2026-10-26. This is the final result from its update, not the interim
300,000-fold count or the manufacturer's 200,000 rating.

`youtube_bot_command_cache` stores the Polish summary, canonical source URL
(without tracking parameters), article title, structured facts, verification date
and original cache timestamp. Startup seeds the entry once and preserves existing
cached content. Replies only read PostgreSQL; they never refetch the article or
require current folding measurements. `make youtube-bot-stats` includes this cache.
The entire article is not stored or reproduced.

The command uses `handler_key=cached_samsung`, its own shared-chat 600-second
cooldown, a matching Polish cooldown disclaimer, and the standard command usage,
deduplication and delivery ledger. `!pomoc` automatically includes it. The full
source URL remains in the cache; the 165-character public reply cites the source
domain to leave room for the required facts and cooldown disclaimer.

## YouTube account-wide posting pause

A provider `rateLimitExceeded` is different from an individual command's cooldown.
`youtube_account_posting_state` persists the authorized channel's `not_before`,
consecutive rejections and last error/success times. The pause covers all commands,
manual posts and automatic stint summaries across projects using that channel.
Command events received during it are marked throttled with `account_backoff`
before invoking a send; no warning/reply is posted. Summaries observed during a pause are recorded as terminal `rejected` with
`account_backoff`; they are never held for later delivery. Messages exceeding
their freshness deadline are also terminal and never replayed later.
The transport also checks the pause before reserving POST quota or opening HTTP.

The default app policy waits approximately 10, 20, 40 and then 60 minutes after
consecutive provider rejections, with positive jitter and any longer Retry-After.
These are conservative local retry times, not a guaranteed Google unblock time.
`youtube_posting_policies` records that policy, and
`youtube_posting_backoff_events` audits each provider rejection and chosen delay.
The worker restores missing account state from already recorded POST history on
startup, preserving the original error times; restart does not reset the pause.
No automatic probe or backlog resend occurs when the timer expires.

The existing API request and delivery records already stored the error reason.
New failures additionally retain sanitized provider messages/domain/Retry-After
in `youtube_api_error_details` and emit an ERROR-level
`youtube_posting_rate_limited` JSON log on **stdout**, flushed immediately,
with `level: ERROR`, request ID, channel, HTTP status, `retry_in_seconds`,
`retry_in` (`HH:MM:SS`), `not_before` and `not_before_warsaw`. Historical raw response text cannot be reconstructed if it was not
saved. Startup logs `posting_cooldown_active` when a pause is in effect.
`make youtube-bot-stats` includes `posting_backoff` and whether it is currently active.

Incident details, retained diagnostics and freshness semantics: [posting rate limits](youtube-rate-limits.md).

Follow Compose errors and active posting cooldown notices with:

```sh
make docker-logs-errors
```

This follows all services, including the YouTube profile, and applies a
line-buffered grep filter. It retains full timestamped error lines and
`posting_cooldown_active` notices, including `not_before`: the earliest permitted
posting attempt as an ISO timestamp with an explicit timezone offset. Ordinary
successful replies with `error_reason: null` are excluded. The deadline is the
app's local pause, not a guarantee YouTube will accept the next post. Press Ctrl+C
to stop following. No Docker rebuild is required for this Make target.

`make docker-logs-errors` first prints the latest persisted `rateLimitExceeded`
for the configured project when the bot container is running, then follows Compose
errors. The initial row is explicitly marked `historical: true`; it includes the
original error time, posting deadline and current local pause flag in Warsaw time.
It also includes `retry_in_seconds` and `retry_in` (`HH:MM:SS`), computed when
you start the command; timestamped live entries show the wait at their log time.
This keeps rate-limit context visible after container recreation removes its old
Compose logs. `local_pause_active: false` means the app's pause expired, not that
Google has confirmed recovery. The display utility is passed to the container on
stdin, so this Make change requires no image rebuild.

Monitor with `docker compose logs -f youtube-bot`. Startup also logs the remaining
wait for an already active pause. Deadlines indicate when a fresh attempt is
allowed locally; YouTube may still reject it. No old replies are retried.

Outgoing messages use `youtube_http.py` with `requests.Session`, matching the
successful authorized diagnostic. Responses (including provider errors) are
explicitly decompressed before bounded JSON parsing. Redirects and automatic POST
retries are disabled. Shared quota/cooldown/expiry and deduplication checks remain
in the normal send path; the diagnostic's cooldown override is not a bot feature.

## One-minute delay for commands from the posting channel

At the user's request, commands whose `author_channel_id` equals the current
posting channel's ID wait **60 seconds after receipt** before reply processing.
This compares stable channel IDs; it does not rely on names, hardcoded owner IDs,
or the viewer's Google account email. Other viewers use the existing immediate
path and normal shared command/account cooldowns.

A separate worker claims due commands, so waiting does not block reading chat.
The existing command event stores `status=delayed`, `reply_delay_seconds=60` and
`reply_not_before`; `delay_claimed_at` records its one-time claim. Usage views
include a `delayed` count. Pending instructions persist across restarts, but
claimed/failed/unknown sends are never retried. The reply text is generated fresh
only when due; measurements are not prepared and queued for a minute.

The worker rechecks command enablement, original publication-age limit (at most
120 seconds), active chat/posting identity and all normal cooldowns. A stale
instruction expires. A newly active account pause suppresses it permanently;
it is not held until the pause expires. A changed posting identity/chat disables
it. No historical rejected/throttled commands are revived by enabling this rule.
This deliberate user-requested short delay does not change the no-stale-reply
policy and is not evidence that a one-minute gap will resolve YouTube restrictions.

`youtube_command_delay_integration_check.py` verifies owner-only scheduling,
other viewers' immediate path, fresh text, durable scheduling, concurrent claim
deduplication, expiry, account backoff and identity changes with simulated sends.
