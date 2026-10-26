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
