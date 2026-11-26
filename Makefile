SHELL := /bin/bash
.DEFAULT_GOAL := help
COMPOSE := docker compose
PY := uv run
RUN := $(COMPOSE) run --rm api

.PHONY: help
help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

# ---------------------------------------------------------------- stack
.PHONY: env
env:
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example: fill GITHUB_TOKEN and SANDBOX_REPO")

.PHONY: build
build: env ## Build images (python image once, then web; never in parallel)
	$(COMPOSE) build api
	$(COMPOSE) build web

.PHONY: up
up: build ## Start the dev stack (docker compose) and apply migrations
	$(COMPOSE) up -d --wait postgres redpanda qdrant
	$(COMPOSE) up -d
	$(RUN) python -m common.migrate

.PHONY: down
down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete volumes
	$(COMPOSE) down -v

.PHONY: logs
logs:
	$(COMPOSE) logs -f --tail=100

.PHONY: check-images
check-images: ## Verify every pinned image exists for linux/arm64
	python3 scripts/check_images.py $$(grep -hoE 'image: *[^ ]+' docker-compose.yml | awk '{print $$2}' | grep -v '^opspilot/' | sort -u) \
	  $$(grep -hoE '^FROM [^ ]+' Dockerfile web/Dockerfile | awk '{print $$2}' | sort -u)

# ---------------------------------------------------------------- quality
.PHONY: lint
lint: ## ruff + mypy + eslint + tsc
	$(PY) ruff check .
	$(PY) ruff format --check .
	$(PY) mypy .
	cd web && npm run lint && npm run typecheck

.PHONY: fmt
fmt:
	$(PY) ruff check --fix .
	$(PY) ruff format .
	cd web && npm run format

.PHONY: test
test: ## Unit tests (python + web)
	$(PY) pytest
	cd web && npm test

.PHONY: test-integration
test-integration: ## Integration tests against the running stack
	$(PY) pytest -m integration
