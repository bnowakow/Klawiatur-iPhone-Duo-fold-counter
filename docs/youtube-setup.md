# Connect Google and use YouTube live chat

## Obtain the OAuth client

1. Open [Google Cloud Console](https://console.cloud.google.com/), create/select a
   project, and note its **Project ID** (not its display name or project number).
2. In **APIs & Services → Library**, find **YouTube Data API v3** and enable it.
3. Open **Google Auth Platform** and configure app branding/contact email. Choose
   **External** audience for a personal Google account and add that account under
   **Audience → Test users** while the app is in Testing.
4. In **Data Access**, add the scope
   `https://www.googleapis.com/auth/youtube.force-ssl`. This grants broad YouTube
   permissions; Google offers no chat-posting-only scope for the insert endpoint.
5. Under **Clients**, create an OAuth client with type **Web application**. Add the
   exact authorized redirect URI `http://localhost:8765/oauth/callback`. No JavaScript
   origin is needed for this command-line application.
6. Download the JSON and save it as `secrets/youtube/client.json` in this checkout.
   The app checks that its project ID and callback match the configuration. This
   directory is excluded from Git and Docker builds, and mounted only into the chat
   service. Keep the JSON on disk; do not paste credentials into chat or commit them.

This follows Google's [web-server OAuth setup](https://developers.google.com/identity/protocols/oauth2/web-server)
and [YouTube authorization guide](https://developers.google.com/youtube/registering_an_application).
Google's console labels may vary. Save the client secret at creation time.

## Connect without sending anything

Create/protect the local directory before downloading/copying the JSON:

```sh
mkdir -p secrets/youtube
chmod 700 secrets secrets/youtube
# After placing the downloaded file:
chmod 600 secrets/youtube/client.json
```

Add or update these values in the existing private `.env` file:

```dotenv
YOUTUBE_PROJECT_ID=YOUR_REAL_CLOUD_PROJECT_ID
YOUTUBE_DAILY_QUOTA_UNITS=10000
YOUTUBE_CHAT_ENABLED=false
```

Then run:

```sh
docker compose --profile youtube build youtube-chat
make youtube-auth
```

Open the printed Google URL in your host browser, select the intended account /
YouTube channel, and consent. The local callback listens for at most five minutes.
Compose publishes port 8765 only on host loopback; inside the container the
listener binds its network interface. No public endpoint or tunnel is required.
The callback state is random and checked, PKCE is enabled, and query strings are
not logged. Only one authorization callback is consumed. `localhost` and
`127.0.0.1` are different redirect/Host values: use the URI above exactly.

This command explicitly configures the project budget, stores credentials in
`secrets/youtube/credentials.json` (0600), identifies the channel and prints its
public ID/title. The identity lookup uses one estimated YouTube API unit.
No message is sent. If identity lookup fails after consent, credentials remain;
resolve the API/channel issue and use `make youtube-status` to retry identification.
A second connection cannot silently overwrite an existing account.

```sh
make youtube-status
```

Status refreshes access credentials if necessary, verifies the channel and resolves
the current video's active live chat. It costs two estimated API units per successful
call (channel lookup + video lookup). It shows local quota usage and never prints
tokens. Missing/ended chat is an error. The first version deliberately resolves
identity and chat on every preview/send rather than caching stale targets.

Refresh credentials survive container recreation. File locking serializes token
refresh, connect, disconnect and CLI sends. In External Testing, YouTube grants can
expire after seven days and need reconnection. A revoked/expired refresh grant
requires browser consent again. OAuth HTTP traffic is separate from YouTube API
quota accounting.

## Manual preview and explicit send

No message content or automatic schedule is configured. When content is chosen,
save UTF-8 text in a host file under `data/`, for example `data/youtube-message.txt`.
The service sees that file as `/data/youtube-message.txt`.

```sh
make youtube-preview MESSAGE_FILE=/data/youtube-message.txt
```

Preview validates 1–200 characters, prints the exact text, channel ID, video ID and
chat ID, and performs no write to YouTube. Trailing file newlines are stripped.
Use the previewed IDs for a later explicit send; changing account/chat requires
another preview. Enable sending in `.env` only when ready:

```dotenv
YOUTUBE_CHAT_ENABLED=true
```

```sh
make youtube-send MESSAGE_FILE=/data/youtube-message.txt REQUEST_KEY=YOUR_UNIQUE_REQUEST_KEY CHANNEL_ID=PREVIEWED_CHANNEL_ID CHAT_ID=PREVIEWED_CHAT_ID
```

A new Compose run reads the current `.env`. Every send requires a nonempty request
key and both expected IDs. Reusing a key with identical contents returns its existing
status; changing its text/target/source is rejected. Do not generate a new key to
retry an uncertain delivery: it might already be visible on YouTube. Each POST
costs an estimated 20 units in addition to discovery reads. There is no automatic
POST retry, including on 5xx responses or timeouts. Definitive HTTP 4xx responses
are recorded as rejected; ambiguous outcomes are recorded as unknown. The returned
JSON status is the delivery result, even when the command exits successfully.

```sh
make youtube-deliveries
docker compose --profile youtube run --rm youtube-chat python youtube_chat.py recover
make youtube-disconnect
```

Recovery reconciles `sending` rows older than five minutes with the API ledger,
marks proven successes as sent and other interrupted outcomes as unknown. It never
resends. Disconnect deletes local credentials first and attempts Google revocation;
its result states whether revocation was confirmed. Stop sends by returning
`YOUTUBE_CHAT_ENABLED=false`; new CLI containers observe that immediately. Any future
long-running worker must reload the switch or restart when it changes.

## Database and operational behavior

`youtube_chat_deliveries` records exact text, request key, target/channel, source,
status, error category, timestamps and YouTube message ID. Text is retained until
explicitly deleted; no automatic cleanup is implemented. Do not put private data
in public messages. `youtube_api_requests.delivery_id` links actual POST attempts
to deliveries; credentials never enter either table. A committed pre-send claim
prevents concurrent duplicate publication. Database loss after successful posting
can leave an uncertain outcome; exactly-once publication is not guaranteed by
YouTube's insert endpoint.

The chat service uses the optional `youtube` Compose profile and one-shot commands.
It does not start a listener/bot by default or interrupt folding analysis.
`make docker-upgrade` also builds its image. No service is connected to Google
until the user initiates authorization. Use the [quota documentation](youtube-live-chat.md)
for policy sources and budget commands.

## Bot command integration

`youtube_commands.py` provides `IncomingMessage`, `CommandRegistry` and `Reply`.
This reusable registry starts empty; the separately enabled `youtube_bot.py`
worker uses its database command configuration to activate `!ile`.
A handler takes `(incoming_message, arguments)` and returns optional reply text;
it can read accepted folding statistics through a separate data access layer.
Dispatch ignores the bot's own channel and derives a stable delivery key from
chat ID and incoming message ID. Responses retain `source_kind='command'` and the
source message ID. Route those fields to `youtube_chat.send()` while holding
`youtube_auth.locked()`; check the reply chat matches the active configured target.
Handlers do not authorize, perform HTTP calls or publish by themselves.

The continuous [bot worker](youtube-bot.md) uses `liveChatMessages.streamList`,
persists incoming command IDs/checkpoints and enforces a global 600-second cooldown.
It answers `!ile` with the compact shared statistics message and the required Polish
disclaimer. Exact command matching prevents its own replies from triggering loops.
Receiving and replying both consume quota. Per-user cooldowns, other handlers and
scheduled broadcasts can be added separately.

## Tests

```sh
docker compose --profile youtube run --rm --no-deps youtube-chat python -m unittest discover -s proof-of-concept -p 'test_youtube_*.py' -v
docker compose --profile youtube run --rm --no-deps youtube-chat python youtube_chat_integration_check.py
docker compose --profile youtube run --rm --no-deps youtube-chat python youtube_quota_integration_check.py
```

The database checks preserve isolated `integration:chat:` / `integration:youtube:`
records and JSON evidence under `data/test-evidence/`. Google responses are mocked;
zero real Google requests or messages are made by these tests. Real browser consent
and a specifically requested test message are still needed for an end-to-end check.

## Reuse the current-person message

`utilities.current_person_message.generate_current_message(stream)` is the shared
read-only generator used by `make docker-message`, YouTube delivery and future bot
handlers. It preserves the existing Polish wording, pace calculation and safeguards
against stale data, uncertain attribution and night breaks. YouTube additionally
checks the 200-character limit and refuses oversized messages without truncation.

```sh
make youtube-preview-current
make youtube-send-current REQUEST_KEY=YOUR_UNIQUE_REQUEST_KEY CHANNEL_ID=PREVIEWED_CHANNEL_ID CHAT_ID=PREVIEWED_CHAT_ID
```

The send command requires the existing explicit enable switch and expected IDs.
It generates fresh statistics at invocation and records the exact posted text with
`source_kind=current_person_message`. A one-time authorized invocation can instead
set `-e YOUTUBE_CHAT_ENABLED=true` on `docker compose run` without enabling subsequent
sends in `.env`. The generated message may change between previews and sends as
new measurements arrive. An existing key is never resent; changed generated text
with that key is rejected. Inspect delivery history before retrying an uncertain
outcome. No automatic schedule is introduced.

Posting failures and account cooldowns persist in PostgreSQL. Old replies are
never retried after the pause; a new command must generate a fresh response.
See [posting limits and retained diagnostics](youtube-rate-limits.md).
