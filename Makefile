.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

PY ?= python3.11
VENV ?= .venv
UV ?= uv

COMPOSE := docker compose -f docker/docker-compose.yml --env-file .env
ENV_FILE := .env

# Default LLM for minimal mode. Override via env: `LLM_MODEL=gemma3:4b make pull-models`.
LLM_MODEL ?= gemma3:1b
LLM_MODEL_EXTRACT ?= gemma3:1b
EMBEDDING_MODEL ?= BAAI/bge-m3

# ============================================================================
# Help
# ============================================================================
.PHONY: help
help: ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "\nSecurGraph RAG — Make targets\n\nUsage: make \033[36m<target>\033[0m\n\n"} \
		/^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)

##@ Environment

.PHONY: env
env: $(ENV_FILE) ## Ensure .env exists (copy from .env.example).
$(ENV_FILE):
	@if [ ! -f $(ENV_FILE) ]; then \
		cp .env.example $(ENV_FILE); \
		echo "Created $(ENV_FILE) from .env.example. Edit if needed."; \
	fi

.PHONY: venv
venv: ## Create .venv and install deps via uv (frozen lockfile).
	@command -v $(UV) >/dev/null 2>&1 || { echo "uv not installed. Install: https://docs.astral.sh/uv/"; exit 1; }
	$(UV) sync --python 3.11 --frozen --extra dev --extra parsers --extra llm_openai

.PHONY: sync
sync: ## Sync deps from uv.lock (frozen).
	$(UV) sync --python 3.11 --frozen --extra dev --extra parsers --extra llm_openai

##@ Backends

.PHONY: up
up: env ## Start backend containers (qdrant, neo4j, openfga, postgres, jaeger).
	$(COMPOSE) up -d
	@$(MAKE) wait-healthy

.PHONY: down
down: ## Stop and remove containers (keeps volumes).
	$(COMPOSE) down

.PHONY: nuke
nuke: ## Stop containers AND delete volumes (DESTRUCTIVE).
	$(COMPOSE) down -v

.PHONY: ps
ps: ## Show container status.
	$(COMPOSE) ps

.PHONY: logs
logs: ## Tail logs from all backends.
	$(COMPOSE) logs -f --tail=200

.PHONY: wait-healthy
wait-healthy: ## Wait for every container healthcheck to be green (max 120s).
	@echo "Waiting for backends to become healthy..."
	@for i in $$(seq 1 60); do \
		unhealthy=$$($(COMPOSE) ps --format json | python3 -c "import sys,json; [print(o['Name']) for line in sys.stdin if line.strip() for o in [json.loads(line)] if o.get('Health') and o['Health']!='healthy']" 2>/dev/null || echo ""); \
		starting=$$($(COMPOSE) ps --format json | python3 -c "import sys,json; [print(o['Name']) for line in sys.stdin if line.strip() for o in [json.loads(line)] if o.get('Health')=='starting']" 2>/dev/null || echo ""); \
		if [ -z "$$unhealthy$$starting" ]; then echo "All backends healthy."; exit 0; fi; \
		echo "  still waiting ($$i/60)..."; sleep 2; \
	done; \
	echo "Timed out waiting for healthy backends."; $(COMPOSE) ps; exit 1

##@ Models (Ollama + BGE-M3)

.PHONY: pull-models
pull-models: ## Pull LLM models via host-native Ollama and pre-download BGE-M3.
	@command -v ollama >/dev/null 2>&1 || { echo "Install Ollama: brew install ollama && ollama serve"; exit 1; }
	ollama pull $(LLM_MODEL)
	@if [ "$(LLM_MODEL)" != "$(LLM_MODEL_EXTRACT)" ]; then ollama pull $(LLM_MODEL_EXTRACT); fi
	$(VENV)/bin/python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('$(EMBEDDING_MODEL)', use_fp16=False); print('BGE-M3 ready')"

##@ App

.PHONY: api
api: env ## Run the FastAPI app with reload.
	$(VENV)/bin/uvicorn securgraph.api.main:app --host 0.0.0.0 --port 8000 --reload

.PHONY: health
health: ## Curl the /health endpoint.
	@curl -s http://localhost:8000/health | python3 -m json.tool

##@ Quality

.PHONY: fmt
fmt: ## Format code (ruff format).
	$(VENV)/bin/ruff format src tests scripts

.PHONY: lint
lint: ## Lint code (ruff check).
	$(VENV)/bin/ruff check src tests scripts

.PHONY: type
type: ## Type-check (mypy).
	$(VENV)/bin/mypy src

.PHONY: test
test: ## Run unit + fast integration tests.
	$(VENV)/bin/pytest -m "unit or integration" -v

.PHONY: test-unit
test-unit: ## Run unit tests only.
	$(VENV)/bin/pytest -m "unit" -v

.PHONY: test-adversarial
test-adversarial: ## Run adversarial property-based tests (security gate).
	$(VENV)/bin/pytest -m "adversarial" -v --hypothesis-seed=0

.PHONY: test-rls-live
test-rls-live: ## Live PostgreSQL RLS gate (requires `make up` + `make migrate`).
	SECURGRAPH_POSTGRES_DSN=$${SECURGRAPH_POSTGRES_MIGRATE_DSN:-postgresql+asyncpg://securgraph:securgraph@localhost:5432/securgraph} \
		$(VENV)/bin/pytest tests/integration/test_postgres_rls.py -q

.PHONY: test-s3-anchor-live
test-s3-anchor-live: ## Live S3 Object Lock anchor gate (requires `--profile anchor` + env vars).
	$(VENV)/bin/pytest tests/integration/test_s3_object_lock_anchor.py -q

.PHONY: mrd-seed-live
mrd-seed-live: ## Seed MRD corpus into a live stack (auth disabled + eval identity mode).
	SECURGRAPH_EVAL_IDENTITY_MODE=true SECURGRAPH_AUTH_ENABLED=false \
		$(VENV)/bin/python eval/mrd_seed_live.py --corpus corpus/mrd_v1

.PHONY: mrd-live
mrd-live: ## Run MRD QA smoke against a live /query endpoint (writes eval/results/).
	$(VENV)/bin/python eval/mrd_qa_smoke.py \
		--live-query-url http://localhost:8000/api/v1/query \
		--tenant-id mrd-lab \
		--output-dir eval/results

.PHONY: test-live-gates
test-live-gates: test-rls-live test-s3-anchor-live ## Run RLS + S3 live gates (MRD run separately via mrd-live).

.PHONY: test-sgh06
test-sgh06: ## SGH-06 cross-store failure injection unit bundle.
	$(VENV)/bin/pytest \
		tests/unit/test_ingestion.py::test_ingestion_failure_injection_postgres_aborts_before_external_stores \
		tests/unit/test_ingestion.py::test_ingestion_failure_injection_qdrant_aborts_before_auth_and_provenance \
		tests/unit/test_ingestion.py::test_ingestion_failure_injection_openfga_aborts_before_graph_and_provenance \
		tests/unit/test_ingestion.py::test_ingestion_failure_injection_neo4j_aborts_before_provenance \
		tests/unit/test_ingest_route_security.py::test_worker_marks_job_failed_after_ingestion_store_failure \
		-q

.PHONY: test-ingest-chaos-live
test-ingest-chaos-live: ## Live ingest reconcile/worker chaos (requires `make up` + `make migrate`).
	$(VENV)/bin/pytest tests/integration/test_ingest_worker_chaos.py -q

.PHONY: cov
cov: ## Test coverage report.
	$(VENV)/bin/pytest --cov=securgraph --cov-report=term-missing --cov-report=html

##@ Database & bootstrap

.PHONY: migrate
migrate: ## Apply Alembic migrations to head (uses superuser DSN).
	SECURGRAPH_POSTGRES_DSN=$${SECURGRAPH_POSTGRES_MIGRATE_DSN:-postgresql+asyncpg://securgraph:securgraph@localhost:5432/securgraph} \
		$(VENV)/bin/alembic upgrade head

.PHONY: seed
seed: ## Seed demo users (dev only).
	$(VENV)/bin/securgraph seed-demo

.PHONY: bootstrap-admin
bootstrap-admin: ## Create the initial super-admin user.
	$(VENV)/bin/securgraph bootstrap-superadmin

##@ Web frontend

.PHONY: web-install
web-install: ## Install web dependencies (npm ci).
	npm ci --prefix web

.PHONY: web-lint
web-lint: ## Lint the web frontend.
	npm --prefix web run lint

.PHONY: web-i18n
web-i18n: ## Check i18n message consistency (en/it).
	npm --prefix web run check:i18n

.PHONY: web-build
web-build: ## Production build of the web frontend.
	npm --prefix web run build

.PHONY: web-e2e
web-e2e: ## Run Playwright E2E tests (needs API + web running).
	npm --prefix web run test:e2e

##@ CI

.PHONY: ci
# test-unit (not test): the unit suite is Docker-free, matching .github/workflows/ci.yml.
ci: lint type test-unit ## Run the same checks as GitHub Actions CI (Docker-free).

##@ Eval (M8)

.PHONY: eval-venv
eval-venv: ## Set up the isolated eval environment (ragas + langchain stack).
	cd eval && $(UV) sync --python 3.11 --frozen

.PHONY: eval
eval: ## Run KPI evaluation against golden set (uses isolated eval/.venv).
	eval/.venv/bin/python scripts/eval.py

.PHONY: consistency-check
consistency-check: ## Cross-store consistency check (Postgres ↔ Qdrant ↔ Neo4j).
	$(VENV)/bin/python scripts/consistency_check.py

.PHONY: baseline
baseline: ## Run vector/lexical/graph/hybrid baseline comparison (writes eval/results/).
	eval/.venv/bin/python eval/baseline.py

##@ Web UI

.PHONY: web-dev
web-dev: ## Run the Next.js development server.
	npm --prefix web run dev

