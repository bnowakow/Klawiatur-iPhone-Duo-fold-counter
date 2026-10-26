.PHONY: docker-upgrade docker-compare docker-message codex-commit

.PHONY: docker-logs-errors
# Preserve full error/cooldown lines, including not_before (next permitted POST).
docker-logs-errors:
	@if [ -n "$$(docker compose --profile youtube ps --status running -q youtube-bot)" ]; then docker compose --profile youtube exec -T youtube-bot python - < utilities/youtube_posting_error_status.py || echo 'ERROR: Could not load stored YouTube errors; continuing with live logs.' >&2; fi
	@bash -o pipefail -c 'docker compose --profile youtube logs -f --no-color --timestamps 2>&1 | grep --line-buffered -Ei '\''rateLimitExceeded|youtube_posting_rate_limited|posting_cooldown_active|worker_backoff|(^|[^[:alnum:]_])(error|fatal|exception|traceback)([^[:alnum:]_]|$$)|"error_reason":[[:space:]]*"[^"]+"'\'''

# Rebuild code, reload .env, and replace application containers.
docker-upgrade:
	docker compose --profile youtube --profile sheets build supervisor ipduotest-pl clothing-descriptions youtube-chat youtube-bot sheets-export sheets-worker
	@bash utilities/stop-legacy-ipduotest-pl.sh
	docker compose up -d --wait db
	docker compose up -d --force-recreate --wait supervisor ipduotest-pl clothing-descriptions
	@if [ -n "$$(docker compose --profile youtube ps --status running -q youtube-bot)" ]; then docker compose --profile youtube up -d --no-deps --force-recreate --wait youtube-bot; fi
	@if [ -n "$$(docker compose --profile sheets ps --status running -q sheets-worker)" ]; then docker compose --profile sheets up -d --no-deps --force-recreate --wait sheets-worker; fi

# Compare published folds/minute with the current person's average.
docker-compare:
	@bash utilities/docker-compare.sh

# Generate a Polish pace/projection message and copy it to the host clipboard.
docker-message:
	@bash utilities/docker-message.sh

.PHONY: docker-video-events
# JSON review bookmarks for detected appearances, folding starts and finishes.
docker-video-events:
	@docker compose exec -T supervisor python presenter_video_events.py

# Stage changes, generate a commit message with Codex, commit, then offer to push.
codex-commit:
	@utilities/codex-commit.sh

.PHONY: youtube-auth youtube-status youtube-preview youtube-send youtube-disconnect youtube-deliveries
# Quote each caller-provided value as a single shell argument.
youtube-quote = '$(subst ','"'"',$(1))'
youtube-auth:
	docker compose --profile youtube run --rm --service-ports youtube-chat python youtube_chat.py connect

youtube-status:
	docker compose --profile youtube run --rm youtube-chat python youtube_chat.py status

# MESSAGE_FILE is a container path under /data; shell values are single-quoted.
youtube-preview:
	docker compose --profile youtube run --rm youtube-chat python youtube_chat.py preview --message-file $(call youtube-quote,$(MESSAGE_FILE))

youtube-send:
	docker compose --profile youtube run --rm youtube-chat python youtube_chat.py send --message-file $(call youtube-quote,$(MESSAGE_FILE)) --request-key $(call youtube-quote,$(REQUEST_KEY)) --expected-channel $(call youtube-quote,$(CHANNEL_ID)) --expected-chat $(call youtube-quote,$(CHAT_ID))

youtube-disconnect:
	docker compose --profile youtube run --rm youtube-chat python youtube_chat.py disconnect

youtube-deliveries:
	docker compose --profile youtube run --rm youtube-chat python youtube_chat.py deliveries

.PHONY: youtube-preview-current youtube-send-current
youtube-preview-current:
	docker compose --profile youtube run --rm youtube-chat python youtube_chat.py preview-current

# Same live-data generator as docker-message; sending still requires explicit opt-in.
youtube-send-current:
	docker compose --profile youtube run --rm youtube-chat python youtube_chat.py send-current --request-key $(call youtube-quote,$(REQUEST_KEY)) --expected-channel $(call youtube-quote,$(CHANNEL_ID)) --expected-chat $(call youtube-quote,$(CHAT_ID))

.PHONY: youtube-bot-up youtube-bot-stop youtube-bot-stats youtube-bot-logs
youtube-bot-up:
	docker compose --profile youtube up -d --build --wait youtube-bot
youtube-bot-stop:
	docker compose --profile youtube stop youtube-bot
youtube-bot-stats:
	docker compose --profile youtube run --rm --no-deps youtube-chat python youtube_bot.py stats
youtube-bot-logs:
	docker compose --profile youtube logs --tail=100 youtube-bot

.PHONY: sheets-build sheets-auth sheets-status sheets-preview sheets-sync sheets-disconnect
sheets-build:
	docker compose --profile sheets build sheets-export sheets-worker
sheets-auth:
	docker compose --profile sheets run --rm --no-deps --service-ports sheets-export python sheets_export.py connect
sheets-status:
	docker compose --profile sheets run --rm --no-deps sheets-export python sheets_export.py status
sheets-preview:
	docker compose --profile sheets run --rm --no-deps sheets-export python sheets_export.py preview
sheets-sync:
	docker compose --profile sheets run --rm --no-deps sheets-export python sheets_export.py sync
sheets-disconnect:
	docker compose --profile sheets run --rm --no-deps sheets-export python sheets_export.py disconnect

.PHONY: sheets-job-up sheets-job-stop sheets-job-status sheets-job-logs
sheets-job-up:
	docker compose --profile sheets up -d --build sheets-worker
sheets-job-stop:
	docker compose --profile sheets stop sheets-worker
sheets-job-status:
	docker compose --profile sheets run --rm --no-deps sheets-export python sheets_jobs.py status
sheets-job-logs:
	docker compose --profile sheets logs --tail=50 sheets-worker
