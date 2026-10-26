# YouTube live chat and quota accounting

The application implements local Google OAuth, private refresh-token storage,
channel identification, live-chat discovery and explicit manual posting. The quota
ledger and HTTP transport are in `youtube_quota.py`; `youtube_auth.py` handles
credentials and `youtube_chat.py` handles delivery. See the
[Google Cloud setup and commands](youtube-setup.md).
No Google account has been connected or real messages sent during implementation.
The [continuous command bot](youtube-bot.md) reads live chat and answers `!ile`
with a persisted ten-minute global cooldown. Scheduled broadcasts are not enabled.

## Published policies

Policies are versioned by verification date in `youtube_api_policies`. The initial
snapshot was verified on 2026-10-26:

| Policy | Value |
| --- | --- |
| Default shared project allowance | 10,000 units/day |
| Reset | Midnight America/Los_Angeles, including daylight saving |
| `liveChatMessages.insert` | 20 units/request |
| `liveChatMessages.list` | 5 units/request |
| `liveChatMessages.streamList` | 5 units/request |
| `videos.list`, `channels.list` | 1 unit/request |
| Chat message length | 200 characters |
| Published viewer posting limit | 11 messages per 30 seconds |
| External OAuth Testing test users | Up to 100 |
| External OAuth Testing authorization lifetime for YouTube scopes | 7 days |

Sources: [API quota costs](https://developers.google.com/youtube/data/determine_quota_cost),
[chat limits](https://support.google.com/youtube/answer/15270973?hl=en),
[OAuth Testing restrictions](https://support.google.com/cloud/answer/15549945?hl=en).
These are a dated snapshot; verify before changing them. Project-specific approved
allowances may differ. At the default budget, 500 posts/day is a theoretical upper
bound if no other shared-budget requests occur. Google's dynamic rate limits,
spam protection, disabled chat, bans, slow mode and membership restrictions can
still prevent posting. The local account guard is not a guarantee of delivery.

## Configuration and reports

`.env` supplies `YOUTUBE_PROJECT_ID` and `YOUTUBE_DAILY_QUOTA_UNITS` to Compose.
Leave the project blank until a real Google Cloud project exists. Do not use the
video ID as a project ID. Explicitly register or update its local budget:

```sh
docker compose run --rm --no-deps supervisor python youtube_quota.py configure --project YOUR_CLOUD_PROJECT_ID --daily-limit 10000
docker compose run --rm --no-deps supervisor python youtube_quota.py status --project YOUR_CLOUD_PROJECT_ID
```

`python youtube_quota.py init` creates the tables and persists the policy snapshot;
it does not connect to Google. `status` without a project prints the policies.
When `YOUTUBE_PROJECT_ID` is set, both commands also require that project to have
been configured. Changing the environment budget alone does not rewrite a stored
budget; run `configure` explicitly.

`youtube_quota_projects` stores actual configured budgets. `youtube_api_requests`
stores each attempted request, its estimated units, Pacific quota date, outcome,
HTTP status, safe error reason and returned resource ID. Credentials and message
text are not stored in this ledger. `youtube_daily_usage` summarizes each project
and quota day. For example:

```sql
SELECT * FROM youtube_daily_usage ORDER BY api_day DESC, project_id;
```

The authenticated transport `tracked_request()` reserves budget in a committed
transaction before network I/O. Concurrent reservations cannot exceed the local
budget. Failed requests and ambiguous outcomes retain their entire estimated
cost conservatively; even a crash before sending may overcount. A timeout after
posting may mean the message was published, so there are no automatic retries.
A successful response followed by a database failure leaves a counted reservation
requiring reconciliation. The account guard counts posting attempts across projects.

This is a **local estimate**, not a measurement of Google's authoritative billed
units. Requests from other applications, manual tools, or code bypassing this
transport are invisible. Use Google Cloud Console to reconcile actual usage.
The existing video-frame downloader and folding supervisor do not call this
transport and do not generate official API usage records. `youtube_stream.py`
accounts for each official `streamList` gRPC connection through the same ledger.

## Connecting a personal account next

1. Create/select a Google Cloud project and enable YouTube Data API v3.
2. Configure its OAuth consent screen; add your account as a test user if Testing.
3. Create an OAuth client and use the implemented browser consent flow with a registered
   local callback, state validation and offline access. The posting endpoint
   accepts the `https://www.googleapis.com/auth/youtube.force-ssl` scope.
4. Persist refresh credentials privately in an ignored persistent location, with
   restrictive permissions; never commit tokens or put them in quota records.
5. Identify the authorized channel (`channels.list` with `mine=true`) and resolve
   the video's current `liveStreamingDetails.activeLiveChatId` using `videos.list`.
6. Send approved message text through `tracked_request()` with
   `liveChatMessages.insert`, `part=snippet`, the active chat ID and authorized
   channel ID. Confirm the returned message ID. Schedule and message content must
   be chosen before enabling automatic posting.

The Google password stays with Google. In Testing, weekly reauthorization is
expected; moving to Production can require Google's OAuth verification. Additional
project quota requires a separate quota/compliance review. See
[OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server),
[posting endpoint](https://developers.google.com/youtube/v3/live/docs/liveChatMessages/insert)
and [quota audits](https://developers.google.com/youtube/data/guides/quota_and_compliance_audits).

## Validation

```sh
docker compose run --rm --no-deps supervisor python -m unittest discover -s proof-of-concept -p test_youtube_quota.py -v
docker compose run --rm --no-deps supervisor python youtube_quota_integration_check.py
```

The integration check uses isolated `integration:youtube:` projects in the real
PostgreSQL database, retains its records and writes JSON evidence under
`data/test-evidence/youtube-quota/<run-id>/`. It sends zero Google requests.

The original design and remaining automation work are described in the
[OAuth and live-chat feature plan](plans/youtube-live-chat.md).
