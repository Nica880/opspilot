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

# ---------------------------------------------------------------- protocols
.PHONY: mcp-stdio
mcp-stdio: ## MCP server over stdio (for Claude Desktop); HTTP is at http://localhost:8000/mcp/
	$(PY) python -m api.mcp_server

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

# ---------------------------------------------------------------- kubernetes (k3d + Argo CD + KEDA)
ARGOCD_VERSION := v3.6.3
K8S_VERSION := 1.35.0
CRD_SCHEMAS := https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json
K8S_IMAGES := rancher/k3s:v1.35.10-k3s1 quay.io/argoproj/argocd:$(ARGOCD_VERSION) \
	ghcr.io/kedacore/keda:2.21.1 ghcr.io/kedacore/keda-metrics-apiserver:2.21.1 \
	ghcr.io/kedacore/keda-admission-webhooks:2.21.1

.PHONY: k8s-up
k8s-up: build ## k3d cluster + Argo CD; Argo CD deploys KEDA, infra and the app from this repo
	k3d cluster create --config deploy/k3d/cluster.yaml
	k3d image import opspilot/py:0.1.0 opspilot/web:0.1.0 -c opspilot
	kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
	kubectl apply -n argocd --server-side --force-conflicts \
	  -f https://raw.githubusercontent.com/argoproj/argo-cd/$(ARGOCD_VERSION)/manifests/install.yaml
	kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=300s
	kubectl create namespace opspilot --dry-run=client -o yaml | kubectl apply -f -
	@if [ -f .env ]; then kubectl -n opspilot create secret generic opspilot-secrets \
	  --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f -; fi
	kubectl apply -f deploy/argocd/root.yaml
	kubectl -n argocd wait --for=jsonpath='{.status.health.status}'=Healthy application/opspilot --timeout=900s
	@echo "UI http://localhost:3000 ? API http://localhost:8000 ? Phoenix http://localhost:6006 ? Temporal http://localhost:8233"
	@echo "Argo CD: kubectl -n argocd port-forward svc/argocd-server 8443:443 (user admin, password:"
	@echo "  kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"

.PHONY: k8s-down
k8s-down: ## Delete the k3d cluster
	k3d cluster delete opspilot

.PHONY: k8s-lint
k8s-lint: ## helm lint + kubeconform (strict, with CRD schemas for KEDA and Argo CD)
	helm lint deploy/helm/opspilot deploy/helm/opspilot-infra --strict
	rm -rf .k8s-render && mkdir -p .k8s-render
	helm template opspilot deploy/helm/opspilot -n opspilot --output-dir .k8s-render > /dev/null
	helm template opspilot-infra deploy/helm/opspilot-infra -n opspilot --output-dir .k8s-render > /dev/null
	kubeconform -strict -summary -kubernetes-version $(K8S_VERSION) -schema-location default \
	  -schema-location '$(CRD_SCHEMAS)' .k8s-render deploy/argocd

.PHONY: check-k8s-images
check-k8s-images: ## Verify the k8s-mode images exist for linux/arm64
	python3 scripts/check_images.py $(K8S_IMAGES)

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
