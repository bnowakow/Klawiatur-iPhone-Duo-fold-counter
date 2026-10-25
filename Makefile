.PHONY: docker-upgrade docker-compare codex-commit

# Rebuild code, reload .env, and replace application containers.
docker-upgrade:
	docker compose build supervisor official-data
	docker compose up -d --wait db
	docker compose up -d --no-deps --force-recreate --wait supervisor official-data

# Compare published folds/minute with the current person's average.
docker-compare:
	@bash utilities/docker-compare.sh

# Stage changes, generate a commit message with Codex, commit, then offer to push.
codex-commit:
	@utilities/codex-commit.sh
