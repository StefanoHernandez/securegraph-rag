# 07 — Deployment

Local and small-team deployment runs on Docker Compose (`docker/docker-compose.yml`) plus the API and web processes. For step-by-step operator instructions (bootstrap, seeding, verification) see the [Operator Deployment Guide](../operator-deployment-guide.md); this document covers the deployment *architecture*: services, profiles, configuration surface, and the production checklist.

## Services and profiles

| Service | Image | Profile | Purpose |
| --- | --- | --- | --- |
| `postgres` | `postgres:16-alpine` | default | Documents, audit spine, identities. |
| `qdrant` | `qdrant/qdrant:v1.18.0` (pinned) | default | Dense vectors + full-text payload index. |
| `neo4j` | `neo4j:5.25-community` | default | Entity graph + communities (512 MB heap default — minimal-hardware mode). |
| `openfga` (+ migrate) | `openfga/openfga:v1.8.0` | default | ReBAC ground truth. |
| `jaeger` | `jaegertracing/all-in-one` | default | Trace backend (OTLP gRPC :4317, UI :16686). |
| `redis` | `redis:7-alpine` | `workers` | arq queue for async ingestion — **optional**; sync ingestion needs no Redis. |
| `minio` (+ init) | `minio/minio` | `anchor` | S3-compatible Object Lock target for Merkle audit anchors (local/dev stand-in for real S3). |
| `ollama` | `ollama/ollama` | `ollama-container` | Containerized Ollama — unused in the current cloud-provider setup. |

```sh
make up                                          # default profile
docker compose -f docker/docker-compose.yml --env-file .env --profile workers up -d   # + async workers
docker compose -f docker/docker-compose.yml --env-file .env --profile anchor up -d    # + anchor target
```

The API (`make api` / uvicorn) and the web app (`make web-dev` or `npm run build && start`) run on the host; the web app proxies `/api/*` to the backend (BFF), so browsers only talk to one origin.

## Configuration surface

All settings live in `src/securgraph/config.py` (env prefix `SECURGRAPH_` unless noted); `.env` is loaded locally and **must never be committed**. Names and semantics only — never put real values in docs or tickets.

### Secrets (SecretStr — never logged, never returned by any endpoint)

| Variable | Purpose |
| --- | --- |
| `JWT_SECRET` | Access-token signing key. Boot refuses default/short values in stage/prod. |
| `AUDIT_HMAC_KEY` | Tamper-chain HMAC key. Env-only, never stored in DB. Unset ⇒ SHA-256 fallback with warning (dev only). |
| `SECURGRAPH_MASTER_KEY` | AES-GCM master key for provider credentials and encrypted ingestion sources. |
| `OPENAI_API_KEY` / `MISTRAL_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | Provider keys via env (prefer the encrypted Settings store in production). |
| `POSTGRES_PASSWORD`, `SECURGRAPH_NEO4J_PASSWORD` | Store passwords. |
| `SECURGRAPH_AUDIT_ANCHOR_S3_SECRET_KEY` | Anchor bucket credential. |

### Core toggles and knobs

| Variable | Default | Notes |
| --- | --- | --- |
| `SECURGRAPH_ENV` | `dev` | `stage`/`prod` activate the fail-closed startup guards. |
| `SECURGRAPH_AUTH_ENABLED` | `true` | Boot **refuses** `false` in stage/prod. |
| `SECURGRAPH_POSTGRES_DSN` | `…securgraph_app@localhost/securgraph` | App connects as the least-privilege `securgraph_app` role. `SECURGRAPH_POSTGRES_MIGRATE_DSN` (superuser) is used by `make migrate`. |
| `SECURGRAPH_LLM_PROVIDER` (+ `_EXTRACT`, `_JUDGE`) | `ollama` | Per-stage providers: generation / graph extraction / NLI judge. Cloud setups must also set matching `SECURGRAPH_LLM_MODEL*` — the coherence guard refuses cloud-provider + Ollama-shaped model names. |
| `SECURGRAPH_EMBEDDING_MODEL` / `_DIM` / `_DEVICE` | `BAAI/bge-m3` / 1024 / cpu | Local embeddings; restart-required (blue-green reindex plan exists in Settings). |
| `SECURGRAPH_FEATURE_WORKERS_ENABLED` | `false` | Async ingestion. `true` without reachable Redis → uploads 503 with an explicit message. |
| `SECURGRAPH_FEATURE_VERIFIER_ENSEMBLE` | `false` | 3-voter NLI ensemble (higher quality, 3× judge calls). |
| `SECURGRAPH_AUDIT_TAMPER_EVIDENT` | `false` | HMAC-chain traces/evidence/decisions. Production: `true`. |
| `SECURGRAPH_FEATURE_SELF_HEALING_ENABLED` | `false` | Background graph contradiction repair. |
| `SECURGRAPH_CONSENSUS_THRESHOLD` | `0.7` | High-trust gate. |
| `SECURGRAPH_RATELIMIT_LOGIN_IP` / `_LOGIN_USER` / `_QUERY` / `_INGEST` | 10 / 5 / 30 / 10 | Per-minute sliding windows. |
| `SECURGRAPH_AUDIT_ANCHOR_S3_BUCKET` / `_ENDPOINT` / `_REGION` / `_PREFIX` | — | Object Lock anchor target; `_INTERVAL_SECONDS` (3600), `_EVENT_THRESHOLD` (10000), `_RETENTION_DAYS` (365). |
| `SECURGRAPH_OTEL_ENABLED` / `_EXPORTER_OTLP_ENDPOINT` | `true` / `:4317` | Tracing; Prometheus metrics on `SECURGRAPH_METRICS_PORT` (9464). |
| `SECURGRAPH_EVAL_IDENTITY_MODE` | unset | **Dev/test only** — never set in production. |

Retrieval knobs: `SECURGRAPH_MAX_GRAPH_DEPTH` (2, hard cap 4), `SECURGRAPH_MAX_EVIDENCE_PER_QUERY` (20), `SECURGRAPH_QUERY_TOKEN_BUDGET` (3000), `SECURGRAPH_LLM_NUM_CTX` (4096).

## Health and readiness

| Endpoint | Cost | Use |
| --- | --- | --- |
| `GET /api/v1/health/live` | zero | Liveness probe. |
| `GET /api/v1/health/ready` | pings postgres/qdrant/neo4j/openfga; provider status from cache | Readiness probe; `down` iff a datastore is unavailable. The LLM provider is never pinged here (cost control) — use the super-admin diagnostics endpoint. |

## Production checklist

- [ ] `SECURGRAPH_ENV=prod`; boot guards verified (JWT secret strong, auth enabled, provider/model coherent).
- [ ] `SECURGRAPH_AUDIT_TAMPER_EVIDENT=true`; `AUDIT_HMAC_KEY` strong and stored only in the runtime environment/secret manager.
- [ ] `SECURGRAPH_MASTER_KEY` set; provider keys entered via Settings UI (encrypted at rest), not `.env`.
- [ ] Real S3 Object Lock bucket for anchors (`COMPLIANCE` mode); MinIO profile is a dev stand-in.
- [ ] PostgreSQL: app role `securgraph_app` in use with a **changed password**, migrations via the separate privileged DSN.
- [ ] Bootstrap super-admin created, password rotated on first login; `seed-demo` **not** run.
- [ ] Cookies: `SECURGRAPH_SESSION_COOKIE_SECURE=true` (automatic in stage/prod) behind TLS.
- [ ] Rate limiter fronted by a shared limiter if running multiple uvicorn workers.
- [ ] `SECURGRAPH_EVAL_IDENTITY_MODE` not set.
- [ ] Release gates green: `make ci`, `make test-adversarial`, `make test-live-gates`, `make mrd-live` (leakage 0, deny 13/13).

## Known deployment gaps (alpha)

- No Helm chart is maintained yet (`deploy/k8s/` scaffolding exists); compose is the supported path.
- Single-process rate limiting; no distributed session cache (sessions live in PostgreSQL — fine at this scale).
- Blue-green embedding reindex: plans can be persisted from Settings, but execution/cutover is future work.
