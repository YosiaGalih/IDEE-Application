# KodeKantor operations (RUNBOOK §2). Production compose: infra/compose.yml with the env file
# KK_ENV_FILE (default .env, created by `make setup`).
KK_ENV_FILE ?= .env
export KK_ENV_FILE
COMPOSE := docker compose -f infra/compose.yml --env-file $(KK_ENV_FILE)

.PHONY: setup sandbox-images workspace config up down ps logs healthcheck

setup: ## env file with fresh secrets, sandbox images, workspace folder
	scripts/gen-secrets.sh
	$(MAKE) sandbox-images workspace

sandbox-images: ## built locally, never pulled (ADR-026 (2))
	docker build -t kodekantor/sandbox-python:1 sandbox-images/python
	docker build -t kodekantor/sandbox-node:1 sandbox-images/node

workspace: ## WORKSPACE_HOST_PATH owned by 10001:10001, mode 0750 (ADR-005); needs root
	scripts/prepare-workspace.sh

config: ## validate the compose file against the env file
	$(COMPOSE) config --quiet && echo "config: infra/compose.yml is valid"

up: ## build our images, run migrations, start everything, wait until healthy
	$(COMPOSE) up -d --build --wait --wait-timeout 300

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps -a

logs: ## make logs SERVICE=api
	$(COMPOSE) logs --tail 200 $(SERVICE)

healthcheck: ## containers healthy + health endpoints through Caddy
	scripts/healthcheck.sh --wait 180
