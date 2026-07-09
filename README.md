# SecurGraph RAG

> **Trustworthy hybrid vector-graph Retrieval-Augmented Generation with verifiable access control and cross-retriever consensus.**

SecurGraph is a research framework that pushes graph-RAG beyond what existing systems (GraphRAG, LightRAG, KGRAG-Ex) provide:

- **C1 — Verifiable Access Correctness.** Every answer ships with a provenance trace that lets an auditor re-derive: *"every chunk/node/edge used as evidence was authorized for the requesting subject at request time."* OpenFGA is ground truth.
- **C2 — Cross-Retriever Consensus as Confidence.** Vector and graph retrievers run independently on authorized evidence. Their agreement (entity overlap + bidirectional NLI on claims) gates "high-trust" answers.
- **C3 — Policy-Aware Community Detection.** The tenant graph is partitioned by structural policy-equivalence-class (canonical hash of `owner + group_viewers + direct_viewers`) **before** Louvain runs, so no community or summary ever spans more than one access class. Retrieval authorizes one OpenFGA check per class plus a per-community defense-in-depth check. The no-leakage guarantee is pinned by a Hypothesis property test under `tests/adversarial/`.
- **C4 — Administrative Control Plane.** Authenticated operators manage users, tenants, groups, access templates, runtime-safe settings, and encrypted provider credentials from the web UI.

This repository implements the full reference framework.

## Status

**Alpha (v0.1.0a0).** Core milestones **M0–M9** and five enterprise extensions are complete (async workers, graph self-healing, hierarchical communities + global RAG, verifier ensemble, tamper-evident audit ledger). Contribution **C3** is implemented under `src/securgraph/community/`.

Active delivery tracks:

| Track | Focus |
| --- | --- |
| **M10** | Security hardening — identity binding, fail-closed audit, tamper-chain concurrency, health cost controls |
| **M11** | Admin control plane — users, tenants, groups, access templates, Settings/Models UI |

Production readiness still requires durable cross-store ingestion, PostgreSQL RLS, S3 Object Lock Merkle anchoring, and unified job control. See [`wiki/project/status.md`](wiki/project/status.md) for the live milestone register.

## Architecture

```
Next.js (Forensic Vault UI) ── BFF proxy ── FastAPI /api/v1
                                              │
                              LangGraph orchestrator
                                              │
        ┌─────────────┬─────────────┬─────────┴─────────┬──────────────┐
        ▼             ▼             ▼                   ▼              ▼
     Qdrant        Neo4j        OpenFGA            PostgreSQL      Redis/arq
   (vector)       (graph)        (ReBAC)         (metadata/audit)  (workers)
        │             │             │                   │
        └─────────────┴─────────────┴───────────────────┴── Ollama / cloud LLMs
                                              │
                              OpenTelemetry → Jaeger
```

| Layer | Default backend | Purpose |
| --- | --- | --- |
| API | FastAPI + Pydantic v2 | Versioned REST under `/api/v1` — ingest, query, explain, audit, auth, control plane, settings |
| AuthN | HttpOnly cookie sessions | Rotating refresh tokens, CSRF on unsafe browser requests, explicit super-admin bootstrap |
| AuthZ | OpenFGA (ReBAC) | Per-user document/chunk/node/edge/community access control |
| Vector store | Qdrant (dense + sparse) | Semantic + BM25-like retrieval |
| Graph store | Neo4j 5 | Entity-relationship traversal, Louvain communities |
| Orchestration | LangGraph `StateGraph` | Vector ‖ graph fanout, RRF fusion, NLI verifier ensemble |
| LLM | Ollama (default) + cloud adapters | Generation, extraction, NLI judge (`openai`, `anthropic`, `gemini`, `mistral`) |
| Embeddings | BGE-M3 (multilingual) | Dense + sparse in one pass |
| Chunker tokenizer | HF `ibm-granite/granite-4.0-h-tiny` | Token budgeting (offline after first cache) |
| Workers | arq + Redis | Async ingestion, community recompute |
| Frontend | Next.js 16 | Forensic Vault UI — chat, ingest, explain, permissions, graph, audit, health, settings, admin |
| Observability | OpenTelemetry → Jaeger | Distributed traces, metrics, audit |

Legacy unversioned route aliases remain for migration but are omitted from OpenAPI; clients and the UI use `/api/v1` only.

## Prerequisites

- **macOS** (tested on Darwin 25.4+) or **Linux**
- **Docker Desktop** with ≥6 GB allocated (Settings → Resources)
- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (`brew install uv` or the official install script)
- **Node.js 20+** and **npm** (for the web UI)
- **Ollama native** (recommended on macOS for Apple Silicon GPU):

  ```sh
  brew install ollama
  ollama serve   # in a separate terminal
  ```

Hardware mode: defaults in `config.py` target **minimal-hardware mode** (<16 GB RAM): `gemma3:1b` LLM, Neo4j with 512 MB heap, etc. Override via `.env`. The tested profile uses `granite4.1:3b` — set `SECURGRAPH_LLM_MODEL=granite4.1:3b` and related keys (see `.env.example`).

## Quickstart

```sh
# 1. Environment
cp .env.example .env

# 2. Python deps
make venv

# 3. Backends (Postgres, Qdrant, Neo4j, OpenFGA, Jaeger, Redis)
make up

# 4. Database schema
.venv/bin/alembic upgrade head

# 5. LLM + embedding models
make pull-models

# 6. First super-admin (one-time; password shown once)
.venv/bin/securgraph bootstrap-superadmin \
  --username admin \
  --display-name "Admin" \
  --password 'change-me-min-12-chars'

# 7. API (terminal 1)
make api

# 8. Web UI (terminal 2)
npm --prefix web install
make web-dev
```

Open **http://localhost:3000**, sign in with the bootstrap account, and change the password when prompted.

Optional dev-only demo accounts (tenant `default`, users `superadmin`, `admin`, `analyst`, `viewer`; passwords `{username}123`, forced reset; `default` tenant includes functional business groups):

```sh
.venv/bin/securgraph seed-demo
```

Health checks:

```sh
curl -s http://localhost:8000/api/v1/health/live | python3 -m json.tool
curl -s http://localhost:8000/api/v1/health/ready | python3 -m json.tool
make health   # legacy alias → /health
```

For encrypted provider credential storage in Settings, set `SECURGRAPH_MASTER_KEY` in `.env` to a long random secret before saving API keys.

## Authentication

- Sessions use **HttpOnly access and refresh cookies**; the browser never stores bearer tokens in `localStorage`.
- The Next.js app proxies `/api/*` to the FastAPI backend (`web/next.config.ts`); unsafe requests require a CSRF token bound to the session.
- The first **super-admin** is created via `securgraph bootstrap-superadmin`; automatic demo seeding on API startup has been removed.
- Roles: `super_admin`, `tenant_admin`, `analyst`, `viewer`. Tenant scope is enforced server-side from membership rows, not from UI dropdowns alone.

Details: [`wiki/project/security-hardening.md`](wiki/project/security-hardening.md), [`wiki/project/admin-control-plane.md`](wiki/project/admin-control-plane.md).

## Web UI

The **Forensic Vault** dashboard (`web/`) covers the evidence lifecycle:

| Area | Route | Purpose |
| --- | --- | --- |
| Briefing | `/` | Dashboard overview |
| Query | `/chat` | Graph-RAG queries with retrieval options |
| Intake | `/ingest` | Document upload with classification and tags |
| Provenance | `/explain` | Trace and custody timeline |
| Clearance | `/permissions` | OpenFGA tuple management |
| Graph | `/graph` | Read-only graph and community view |
| Custody Ledger | `/audit` | Trace list and tamper-chain verification |
| Health | `/health` | Component readiness (cached diagnostics) |
| Settings / Models | `/settings`, `/models` | Runtime keys and provider credentials |
| Admin | `/users`, `/tenants`, `/groups`, `/access-templates` | Control-plane management |

Build for production:

```sh
npm --prefix web run build -- --webpack
```

## API overview

Primary endpoints (all under `/api/v1`):

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/auth/login`, `/auth/refresh`, `/auth/logout` | Session lifecycle |
| `GET` | `/auth/me`, `/auth/sessions` | Profile and active sessions |
| `POST` | `/ingest` | Document ingestion |
| `POST` | `/query` | Hybrid RAG query |
| `GET` | `/explain/{trace_id}` | Provenance and authorization replay |
| `GET` | `/audit/traces`, `POST` `/audit/verify-chain` | Audit ledger |
| `GET/POST` | `/tenants`, `/users`, `/groups`, `/access-templates` | Control plane |
| `GET` | `/health/live`, `/health/ready` | Liveness and readiness |

Interactive docs: **http://localhost:8000/docs**

## Project layout

```
src/securgraph/
  domain/           # Pydantic lingua franca models
  adapters/         # Protocol-based backends (LLM, Neo4j, Qdrant, Postgres, …)
  ingestion/        # Parsers, chunker, async worker, self-healing
  indexing/         # Postgres/Qdrant/Neo4j writers and retrievers, tamper chain
  community/        # C3 policy-equivalence-class partitioning
  orchestration/    # LangGraph query workflow
  provenance/       # Trace assembly for /explain
  observability/    # OpenTelemetry
  security/         # AES-GCM credential encryption
  services/         # Provider catalog helpers
  api/              # FastAPI routes, middleware, dependencies
  cli.py            # bootstrap-superadmin, seed-demo
tests/{unit,integration,adversarial}/
corpus/             # Seed, permissions, and golden evaluation data
eval/               # Isolated ragas/baseline harness (own lockfile)
scripts/            # Eval helpers, consistency checks
docs/{research,superpowers}/
web/                # Next.js Forensic Vault UI
wiki/               # Engineering wiki (architecture, modules, status)
docker/             # Local compose stack
alembic/            # PostgreSQL migrations
```

## Development

```sh
make lint          # ruff check
make type          # mypy on src/
make test-unit     # unit tests (~120+)
make test          # unit + integration
make test-adversarial   # C3 no-leakage property tests
make ci            # lint + type + test
make baseline      # vector / lexical / graph / hybrid comparison → eval/results/
make eval          # golden-set KPI evaluation (requires make eval-venv)
```

Engineering reference: [`wiki/engineering/development.md`](wiki/engineering/development.md), [`wiki/index.md`](wiki/index.md).

## Evaluation


Empirical results: `make eval` on the golden set; multi-mode baselines via `make baseline`.

## License

Apache-2.0 (planned at v0.1.0 release).

## Citation

(TBD at v0.1.0)
