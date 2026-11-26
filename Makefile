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
	$(COMPOSE) exec -T redpanda rpk topic create issues.v1 docs.v1 -p 6 || true
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

# ---------------------------------------------------------------- data
.PHONY: ingest
ingest: ## Backfill issues, PRs, comments, discussions and docs (MODE=small|full)
	$(RUN) python -m ingestion.cli backfill
	$(RUN) python -m ingestion.cli docs

.PHONY: ingest-incremental
ingest-incremental: ## Fetch everything updated since the stored cursors
	$(RUN) python -m ingestion.cli incremental

.PHONY: record-fixtures
record-fixtures: ## Re-record GitHub fixtures (unauthenticated)
	$(PY) python scripts/record_fixtures.py

.PHONY: index
index: ## Rebuild the Qdrant index from Postgres (the indexer consumer keeps it fresh afterwards)
	$(RUN) python -m indexing.cli reindex

.PHONY: search
search: ## make search Q="dependency with yield RuntimeError"
	$(RUN) python -m indexing.cli search "$(Q)"

.PHONY: eval-retrieval
eval-retrieval: ## Duplicate recall@k / MRR: dense vs hybrid vs hybrid+rerank -> docs/eval/
	$(RUN) python -m evals.retrieval

# ---------------------------------------------------------------- agent
.PHONY: demo
demo: ## Small-mode demo: ingest ~2k issues, index, triage the 5 newest open issues
	$(MAKE) ingest
	$(MAKE) index
	$(RUN) python -m workflows.demo --n $${N:-5}
	@echo "UI: http://localhost:3000  ·  Temporal: http://localhost:8233  ·  Phoenix: http://localhost:6006"

.PHONY: triage
triage: ## make triage N=10: start triage on the N newest open issues
	$(RUN) python -m workflows.demo --n $${N:-5}

# ---------------------------------------------------------------- evals
.PHONY: eval
eval: ## Retrieval eval + triage matrix (2 models x 2 prompts x 3 retrieval) on 50 held-out issues each
	$(RUN) python -m evals.retrieval
	$(RUN) python -m evals.harness --limit $${LIMIT:-50} --judge-sample $${JUDGE:-20}

.PHONY: eval-full
eval-full: ## Default config on 1,000 held-out issues with 100 judged drafts (hours on a laptop)
	$(RUN) python -m evals.harness --models qwen2.5:7b-instruct --prompts v2 --variants hybrid_rerank \
	  --limit 1000 --judge-sample 100

.PHONY: redteam
redteam: ## 50 crafted attacks against the real model; must pass 100%
	$(RUN) python -m evals.redteam.run

.PHONY: promptfoo
promptfoo: ## promptfoo regression suite against Ollama (CI runs it with the fake LLM)
	cd evals/promptfoo && PROMPTFOO_PYTHON=$(CURDIR)/.venv/bin/python LLM_PROVIDER=$${LLM_PROVIDER:-ollama} \
	  npx --yes promptfoo@0.126.0 eval -c promptfooconfig.yaml --no-cache

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
