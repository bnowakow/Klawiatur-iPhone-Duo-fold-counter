.PHONY: docker-upgrade docker-compare docker-message codex-commit

# Rebuild code, reload .env, and replace application containers.
docker-upgrade:
	docker compose --profile youtube build supervisor official-data clothing-descriptions youtube-chat
	docker compose up -d --wait db
	docker compose up -d --force-recreate --wait supervisor official-data clothing-descriptions

# Compare published folds/minute with the current person's average.
docker-compare:
	@bash utilities/docker-compare.sh

# Generate a Polish pace/projection message and copy it to the host clipboard.
docker-message:
	@bash utilities/docker-message.sh

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
