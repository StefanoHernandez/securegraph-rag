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
| `redis` | `redis:7-alpine` | `workers` | arq queue for ingestion saga and periodic anchoring. Optional only in local development; required in stage/prod. |
| `minio` (+ init) | `minio/minio` | `anchor` | Self-hosted MinIO Object Lock target for integration tests. Production uses a distinct MinIO deployment. |
| `ollama` | `ollama/ollama` | `ollama-container` | Explicit legacy local-adapter profile; excluded from defaults, acceptance, and experiments. |

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
| `POSTGRES_PASSWORD`, `SECURGRAPH_NEO4J_PASSWORD` | Store passwords. Boot refuses the known-public compose defaults in stage/prod. |
| `SECURGRAPH_OPENFGA_API_TOKEN` | Preshared key for the OpenFGA API; must match the server's `OPENFGA_AUTHN_PRESHARED_KEYS`. Boot refuses an empty value in stage/prod. |
| `SECURGRAPH_QDRANT_API_KEY` | Qdrant API key (`QDRANT__SERVICE__API_KEY` on the server). Boot refuses an empty value in stage/prod. |
| `SECURGRAPH_REDIS_PASSWORD` | Redis `requirepass` value; the DSN in `SECURGRAPH_REDIS_URL` must carry it. |
| `SECURGRAPH_AUDIT_ANCHOR_SECRET_KEY` | Dedicated MinIO Object Lock credential. |

### Core toggles and knobs

| Variable | Default | Notes |
| --- | --- | --- |
| `SECURGRAPH_ENV` | `dev` | `stage`/`prod` activate the fail-closed startup guards. |
| `SECURGRAPH_BIND_ADDR` | `127.0.0.1` | Interface the compose stack publishes datastore ports on. Loopback by default — see "Datastore hardening" below. |
| `SECURGRAPH_TRUSTED_PROXIES` | empty | Addresses/CIDRs whose `X-Forwarded-For` is believed. Empty ignores the header. See "Client addresses behind the BFF". |
| `SECURGRAPH_TRUST_PROXY_HEADERS` (web app) | `false` | Whether the BFF forwards inbound `X-Forwarded-For`/`X-Real-IP`/`Forwarded`. Enable only behind a reverse proxy that overwrites them. |
| `SECURGRAPH_AUTH_ENABLED` | `true` | Boot **refuses** `false` in stage/prod. |
| `SECURGRAPH_POSTGRES_DSN` | `…securgraph_app@localhost/securgraph` | App connects as the least-privilege `securgraph_app` role. `SECURGRAPH_POSTGRES_MIGRATE_DSN` (superuser) is used by `make migrate`. |
| `SECURGRAPH_LLM_PROVIDER` (+ `_EXTRACT`, `_JUDGE`) | `mistral` / `mistral` / `openai` | Per-stage providers: generation / graph extraction / NLI judge. Defaults are `mistral-small-latest`, `mistral-small-latest`, and `gpt-4o-mini`; Ollama is rejected in stage/prod. |
| `SECURGRAPH_EMBEDDING_MODEL` / `_DIM` / `_DEVICE` | `BAAI/bge-m3` / 1024 / cpu | Local embeddings; restart-required (blue-green reindex plan exists in Settings). |
| `SECURGRAPH_FEATURE_WORKERS_ENABLED` | `false` in dev | Enables ARQ ingestion and periodic anchors. It is mandatory in stage/prod; a reachable Redis/worker deployment is therefore required there. |
| `SECURGRAPH_FEATURE_VERIFIER_ENSEMBLE` | `false` | 3-voter NLI ensemble (higher quality, 3× judge calls). |
| `SECURGRAPH_AUDIT_TAMPER_EVIDENT` | `false` | HMAC-chain traces/evidence/decisions. Production: `true`. |
| `SECURGRAPH_FEATURE_SELF_HEALING_ENABLED` | `false` | Background graph contradiction repair. |
| `SECURGRAPH_CONSENSUS_THRESHOLD` | `0.7` | High-trust gate. |
| `SECURGRAPH_RATELIMIT_LOGIN_IP` / `_LOGIN_USER` / `_QUERY` / `_INGEST` | 10 / 5 / 30 / 10 | Per-minute sliding windows. |
| `SECURGRAPH_AUDIT_ANCHOR_BUCKET` / `_ENDPOINT` / `_REGION` / `_PREFIX` | — | Canonical MinIO Object Lock target; `_ACCESS_KEY`, `_SECRET_KEY`, `_INTERVAL_SECONDS` (3600), `_EVENT_THRESHOLD` (10000), `_RETENTION_DAYS` (365). Deprecated `_S3_*` aliases remain only for upgrade compatibility. |
| `SECURGRAPH_EVAL_BASELINE_TEMPLATES` | unset | **Dev MRD only.** Enables deterministic synthesis templates in `try_deterministic_synthesis`. Must be **off** for held-out eval (`corpus/mrd_heldout_v1`). |
| `SECURGRAPH_OTEL_ENABLED` / `_EXPORTER_OTLP_ENDPOINT` | `true` / `:4317` | Tracing; Prometheus metrics on `SECURGRAPH_METRICS_PORT` (9464). |
| `SECURGRAPH_EVAL_IDENTITY_MODE` | unset | **Dev/test only** — never set in production. |

Retrieval knobs: `SECURGRAPH_MAX_GRAPH_DEPTH` (2, hard cap 4), `SECURGRAPH_MAX_EVIDENCE_PER_QUERY` (20), `SECURGRAPH_QUERY_TOKEN_BUDGET` (3000), `SECURGRAPH_LLM_NUM_CTX` (4096).

## Health and readiness

| Endpoint | Cost | Use |
| --- | --- | --- |
| `GET /api/v1/health/live` | zero | Liveness probe. |
| `GET /api/v1/health/ready` | pings postgres/qdrant/neo4j/openfga; provider status from cache | Readiness probe; `down` iff a datastore is unavailable. The LLM provider is never pinged here (cost control) — use the super-admin diagnostics endpoint. |

## WORM anchoring trust boundary

| Environment | Anchor target | Trust model |
| --- | --- | --- |
| **Local Compose** (`--profile anchor`) | MinIO on localhost | Integration gate only — proves the anchor adapter and Object Lock wiring; same failure domain as the dev stack. |
| **Stage / production** | Dedicated self-hosted MinIO Object Lock on a **separate host and administrative domain** | Independently operated commitment; `enforce_audit_hygiene()` refuses startup without HMAC, endpoint, bucket, and dedicated credentials. |

Compose MinIO is **not** a production trust anchor. A MinIO instance colocated with the API or database is not an external boundary either. Production uses a separately administered self-hosted MinIO deployment, a dedicated Object Lock bucket in `COMPLIANCE` mode with 365-day retention, and dedicated credentials. The periodic ARQ job `anchor_due_ledgers` calls the same due-anchor logic as the operations API.

## Datastore hardening

Every guarantee this system makes is enforced one layer *above* the datastores:
OpenFGA decides authorization (C1), PostgreSQL RLS isolates tenants, and Qdrant
holds the chunk text those checks exist to gate. A store that accepts anonymous
callers makes all of it decorative — an attacker writes their own OpenFGA
tuples, or reads chunk payloads straight out of Qdrant, and the audit ledger
still records the access as authorized.

`enforce_datastore_hygiene()` refuses to boot in stage/prod when any of the
following is true:

| Condition | Why it is fatal |
| --- | --- |
| `SECURGRAPH_OPENFGA_API_TOKEN` empty | The ReBAC ground truth would accept unauthenticated tuple writes. |
| `SECURGRAPH_QDRANT_API_KEY` empty | Chunk payloads become readable with no OpenFGA check in the path. |
| `SECURGRAPH_NEO4J_PASSWORD` empty or the compose default | Direct graph read/write, bypassing path-cut pruning. |
| `SECURGRAPH_POSTGRES_DSN` with no password or known-public credentials | RLS does not protect a database an attacker can log into directly. |
| `SECURGRAPH_REDIS_URL` without a password (workers enabled) | The ingest queue carries tenant document payloads. |

Compose defaults, for reference:

- Ports publish on `${SECURGRAPH_BIND_ADDR:-127.0.0.1}` — loopback. Raise it only for a deliberately firewalled host, never on a public interface.
- OpenFGA runs `OPENFGA_AUTHN_METHOD=preshared`; the playground is **off** (it requires `authn=none`).
- Neo4j loads **no plugins**. Louvain runs in `networkx` (`adapters/community_generator.py`) and the writer is deliberately APOC-free, so the former `procedures_unrestricted: gds.*,apoc.*` only ever supplied `apoc.load.*` — outbound HTTP/JDBC/file reads from inside the database — to any Cypher foothold.
- Credentials use `${VAR:?message}`, not `${VAR:-default}`: a missing value stops compose instead of silently falling back to a known-public default.

Loopback binding is a local-development control, not a substitute for network
policy. A real deployment puts these stores on a private network with TLS
between the API and each of them; none of the compose services terminate TLS.

## Client addresses behind the BFF

Browsers reach the API through the Next.js BFF, so `request.client.host` is
the proxy on every request. Two things depend on the caller's real address:
the per-IP login limiter (`SECURGRAPH_RATELIMIT_LOGIN_IP`) and the
`ip_address` recorded on each session row, which `GET /auth/sessions` shows
users so they can recognise a login that is not theirs. Left uncorrected,
both collapse to one value: the limiter becomes a single global budget that
one attacker can exhaust for every user, and every session looks identical.

Believing `X-Forwarded-For` unconditionally would be worse than the bug — a
caller who sets the header picks a fresh address per request and is never
limited at all. Resolution therefore requires an explicitly configured chain:

| Layer | Setting | Effect |
| --- | --- | --- |
| API | `SECURGRAPH_TRUSTED_PROXIES` | Addresses/CIDRs whose forwarded header is believed. Empty (default) ignores the header entirely. |
| Web BFF | `SECURGRAPH_TRUST_PROXY_HEADERS` | `false` (default) strips inbound `X-Forwarded-For`/`X-Real-IP`/`Forwarded`; `true` forwards them. |

`securgraph.api.client_address.client_ip()` walks the forwarded chain
right-to-left past trusted hops and takes the first untrusted entry — the
closest address to the client that a trusted proxy actually observed. Values
a caller prepends sit to the left of that and are never reached. An entry
that fails to parse, or a chain of exclusively trusted hops, falls back to
the peer address.

Both flags must agree. Enable them only when a reverse proxy that
**overwrites** these headers sits in front of Next: a Next route handler
exposes no peer address, so the BFF cannot append a trustworthy hop itself.
With neither set — the default — per-client granularity is lost but nothing
is spoofable, and `SECURGRAPH_RATELIMIT_LOGIN_USER` still bounds brute force
per account.

The limiter remains in-process (see `securgraph/api/ratelimit.py`): correct
for the documented single-worker deployment, and still requiring a shared
store before scaling out.

## Production checklist

- [ ] `SECURGRAPH_ENV=prod`; boot guards verified (JWT secret strong, auth enabled, provider/model coherent, datastores authenticated).
- [ ] Datastore hardening: OpenFGA preshared key, Qdrant API key, rotated Neo4j/Postgres/Redis passwords, stores on a private network, no port published on a public interface.
- [ ] `SECURGRAPH_AUDIT_TAMPER_EVIDENT=true`; `AUDIT_HMAC_KEY` strong and stored only in the runtime environment/secret manager.
- [ ] `SECURGRAPH_MASTER_KEY` set; provider keys entered via Settings UI (encrypted at rest), not `.env`.
- [ ] Dedicated self-hosted MinIO Object Lock bucket for anchors (`COMPLIANCE`, 365 days), on a separate host and administrative domain; Compose MinIO is dev/integration only.
- [ ] `SECURGRAPH_FEATURE_WORKERS_ENABLED=true`, Redis, and an ARQ worker deployment are live for the durable ingest saga and periodic anchors.
- [ ] PostgreSQL: app role `securgraph_app` in use with a **changed password**, migrations via the separate privileged DSN.
- [ ] Bootstrap super-admin created, password rotated on first login; `seed-demo` **not** run.
- [ ] Cookies: `SECURGRAPH_SESSION_COOKIE_SECURE=true` (automatic in stage/prod) behind TLS.
- [ ] Rate limiter fronted by a shared limiter if running multiple uvicorn workers.
- [ ] `SECURGRAPH_TRUSTED_PROXIES` and `SECURGRAPH_TRUST_PROXY_HEADERS` set consistently behind a header-overwriting reverse proxy, or both left unset.
- [ ] `SECURGRAPH_OPENFGA_STORE_ID` and `SECURGRAPH_OPENFGA_AUTHORIZATION_MODEL_ID` pinned to the provisioned values — startup refuses to create either.
- [ ] `SECURGRAPH_ALLOWED_HOSTS` set to the served hostnames.
- [ ] `SECURGRAPH_EVAL_IDENTITY_MODE` and `SECURGRAPH_EVAL_BASELINE_TEMPLATES` not set.
- [ ] Release gates green: `make ci`, `make cov`, `make test-adversarial`, `make test-live-gates`, `make heldout-live` (0 leaks), `make mrd-live` (dev security 13/13).

## Known deployment gaps (alpha)

- No Helm chart is maintained yet (`deploy/k8s/` scaffolding exists); compose is the supported path.
- Single-process rate limiting; no distributed session cache (sessions live in PostgreSQL — fine at this scale).
- Blue-green embedding reindex: plans can be persisted from Settings, but execution/cutover is future work.
