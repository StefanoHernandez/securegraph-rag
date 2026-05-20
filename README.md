# SecurGraph RAG

> **Trustworthy hybrid vector-graph Retrieval-Augmented Generation with verifiable access control and cross-retriever consensus.**

SecurGraph is a research framework that pushes graph-RAG beyond what existing systems (GraphRAG, LightRAG, KGRAG-Ex) provide:

- **C1 — Verifiable Access Correctness.** Every answer ships with a provenance trace that lets an auditor re-derive: *"every chunk/node/edge used as evidence was authorized for the requesting subject at request time."* OpenFGA is ground truth.
- **C2 — Cross-Retriever Consensus as Confidence.** Vector and graph retrievers run independently on authorized evidence. Their agreement (entity overlap + bidirectional NLI on claims) gates "high-trust" answers.
- **C3 — Policy-Aware Community Detection.** Per policy-equivalence-class community detection — prevents the cross-class structural leakage that GraphRAG-style global summaries cause.

This repository implements the full reference framework.

## Status

🚧 **Alpha — Milestone 4 (Policy Enforcement) in progress.** M0, M1, M2, and M3 are complete. See `wiki/project/milestones.md` for the M0→M8 plan.

## Architecture

```
FastAPI ── LangGraph orchestrator ── {Qdrant, Neo4j, OpenFGA, Postgres, Redis, Ollama}
                                  └── Adapters (Protocol-based, no vendor lock-in)
                                  └── OpenTelemetry → Jaeger
```

Components:

| Layer            | Default backend          | Purpose                                          |
| ---------------- | ------------------------ | ------------------------------------------------ |
| API              | FastAPI + Pydantic v2    | `/ingest` `/query` `/explain` `/audit` `/admin`  |
| Vector store     | Qdrant (dense + sparse)  | Semantic + BM25-like retrieval                   |
| Graph store      | Neo4j 5 + GDS plugin     | Entity-relationship traversal, communities       |
| Authorization    | OpenFGA (ReBAC)          | Per-user 5-level access control                  |
| Orchestration    | LangGraph `StateGraph`   | Vector ‖ Graph fanout, consensus, verifier       |
| LLM              | Ollama (Gemma 3 local)   | Generation, extraction, NLI judge                |
| Embeddings       | BGE-M3 (multilingual)    | Dense + sparse in one pass                       |
| Workers (M6+)    | Arq + Redis              | Async ingestion, community recompute             |
| Frontend (M6+)   | Next.js 15 + react-flow  | Chat, explain, admin, audit views                |
| Observability    | OpenTelemetry → Jaeger   | Distributed traces, metrics, audit               |

## Prerequisites

- **macOS** (tested on Darwin 25.4+) or Linux.
- **Docker Desktop** with ≥6 GB allocated (Settings → Resources).
- **Python 3.11+**.
- **[uv](https://docs.astral.sh/uv/)** package manager (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- **Ollama native** (recommended on macOS for Apple Silicon GPU):
  ```sh
  brew install ollama
  ollama serve   # in a separate terminal
  ```

Hardware mode: this repo defaults to **minimal-hardware mode** (<16 GB RAM): `gemma3:1b` LLM, Neo4j with 512 MB heap, etc. For higher tiers override via `.env`.

## Quickstart

```sh
# 1. Create environment file
cp .env.example .env

# 2. Create virtualenv and install deps
make venv

# 3. Start backends
make up

# 4. Pull LLM and embedding models
make pull-models

# 5. Run the API
make api

# 6. Health-check in another terminal
make health
```

Expected output: `{"status": "ok", "components": {...}}` with each backend reporting OK.

## Project Layout

```
src/securgraph/             # framework code
  domain/                   # Pydantic data objects (lingua franca)
  adapters/                 # Protocol-based backend adapters
  ingestion/ extraction/    # M1, M3
  indexing/                 # writers
  policy/                   # M4 — OpenFGA integration
  retrieval/                # M2, M3, M5
  consensus/ verifier/      # M4.5, M7
  generation/               # M5
  orchestration/            # LangGraph state machine
  provenance/ audit/        # M6, M7
  observability/            # OpenTelemetry
  community/                # M6 — C3
  workers/                  # M6 — Arq tasks
  limits/                   # M7
  testing/                  # adversarial harness, corpus gen
tests/{unit,integration,adversarial}/
corpus/{seed,permissions,golden}/
scripts/                    # eval, baselines, figures, load test
docs/{eval,research,sprints,paper}/
deploy/k8s/                 # Helm chart (M8)
web/                        # Next.js 15 frontend (M6+)
wiki/                       # Local project wiki and engineering documentation
```

## Evaluation


Empirical results are produced by `make eval` on the golden set.

## License

Apache-2.0 (planned at v0.1.0 release).

## Citation

(TBD at v0.1.0)
