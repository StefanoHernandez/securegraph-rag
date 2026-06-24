# SecurGraph RAG

> **Trustworthy hybrid vector-graph Retrieval-Augmented Generation with verifiable access control and cross-retriever consensus.**

SecurGraph is a research framework that pushes graph-RAG beyond what existing systems (GraphRAG, LightRAG, KGRAG-Ex) provide:

- **C1 — Verifiable Access Correctness.** Every answer ships with a provenance trace that lets an auditor re-derive: *"every chunk/node/edge used as evidence was authorized for the requesting subject at request time."* OpenFGA is ground truth.
- **C2 — Cross-Retriever Consensus as Confidence.** Vector and graph retrievers run independently on authorized evidence. Their agreement (entity overlap + bidirectional NLI on claims) gates "high-trust" answers.
- **C3 — Policy-Aware Community Detection.** The tenant graph is partitioned by structural policy-equivalence-class (canonical hash of `owner + group_viewers + direct_viewers`) **before** Louvain runs, so no community or summary ever spans more than one access class. Retrieval authorizes one OpenFGA check per class plus a per-community defense-in-depth check. The no-leakage guarantee is pinned by a Hypothesis property test under `tests/adversarial/`.

This repository implements the full reference framework.

## Status

🚧 **Alpha.** All core milestones M0–M8 are complete and five enterprise extensions have landed (EXT1 async workers, EXT2 graph self-healing, EXT3 hierarchical communities + global RAG, EXT4 verifier ensemble, EXT5 tamper-evident audit ledger). Contribution C3 (policy-partitioned community detection) is implemented under `src/securgraph/community/`. See `wiki/project/milestones.md` and `wiki/overview-securegraph-rag.md` for the current state.

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
| LLM              | Ollama (Granite 4.1 default; Gemma 3 minimal-hardware) | Generation, extraction, NLI judge                |
| Embeddings       | BGE-M3 (multilingual)    | Dense + sparse in one pass                       |
| Chunker tokenizer | HF `ibm-granite/granite-4.0-h-tiny` | Token budgeting (offline after first cache) |
| Workers          | Arq + Redis              | Async ingestion, community recompute             |
| Frontend         | Next.js 16 + @xyflow/react + react-force-graph-3d (built) | Dashboard, chat, ingest, explain, permissions, graph, health, audit views |
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

Hardware mode: the Python defaults in `config.py` target **minimal-hardware mode** (<16 GB RAM): `gemma3:1b` LLM, Neo4j with 512 MB heap, etc. Override via `.env`; the tested production profile uses `granite4.1:3b` (set `SECURGRAPH_LLM_MODEL=granite4.1:3b` and friends, see `.env.example`).

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
  community/                # C3 — policy-equivalence-class partitioning
  workers/                  # Arq tasks (async ingestion, recompute)
  limits/                   # M7
  testing/                  # adversarial harness, corpus gen
tests/{unit,integration,adversarial}/
corpus/{seed,permissions,golden}/
scripts/                    # eval, baselines, figures, load test
docs/{eval,research,sprints,paper}/
deploy/k8s/                 # Helm chart (planned)
web/                        # Next.js frontend (built — Forensic Vault UI)
wiki/                       # Local project wiki and engineering documentation
```

## Evaluation


Empirical results are produced by `make eval` on the golden set.

## License

Apache-2.0 (planned at v0.1.0 release).

## Citation

(TBD at v0.1.0)
