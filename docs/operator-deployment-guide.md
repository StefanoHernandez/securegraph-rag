# SecureGraph RAG — Operator Deployment Guide (D8)

**Version:** v0.1.0-rc1

## Prerequisites

- **macOS** (Darwin 25.4+) or **Linux**
- **Docker Desktop** with ≥6 GB allocated
- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (`brew install uv`)
- **Node.js 20+** and **npm**
- **Mistral API key** for generation and extraction, plus an **OpenAI API key**
  for the NLI/RAGAS judge

## Step-by-Step Deployment

### 1. Clone and configure environment

```sh
git clone <repo-url> SecureGraph
cd SecureGraph
cp .env.example .env
```

Edit `.env`:
- Keep the default providers: `mistral` for generation/extraction and `openai`
  for the judge
- Set `MISTRAL_API_KEY` and `OPENAI_API_KEY`
- Set `SECURGRAPH_MASTER_KEY` to a long random secret (for encrypted
  provider credential storage)
- Set `AUDIT_HMAC_KEY` to a 32+ byte hex string (for tamper-evident audit
  chain)

### 2. Start backend containers

```sh
make up
```

This starts PostgreSQL, Qdrant, Neo4j, OpenFGA, Jaeger, and (with
`--profile anchor`) MinIO for Object Lock integration testing.

On first run, `make up` copies `docker/postgres-init/init-openfga.sql.example`
to `init-openfga.sql` if missing, and initializes the Qdrant collection.

To enable the local MinIO Object Lock integration target:

```sh
docker compose -f docker/docker-compose.yml --env-file .env --profile anchor up -d
```

Then set in `.env`:
```
SECURGRAPH_AUDIT_ANCHOR_BUCKET=securgraph-audit-anchors
SECURGRAPH_AUDIT_ANCHOR_ENDPOINT=http://localhost:9000
SECURGRAPH_AUDIT_ANCHOR_ACCESS_KEY=minioadmin
SECURGRAPH_AUDIT_ANCHOR_SECRET_KEY=minioadmin
```

For stage/prod, use a dedicated self-hosted MinIO Object Lock deployment on a
separate host and administrative domain. Set its endpoint and dedicated
credentials; retention is `COMPLIANCE` for 365 days. Do not use the Compose
instance as a production trust boundary.

### 3. Install Python dependencies

```sh
make venv
```

### 4. Apply database migrations

```sh
.venv/bin/alembic upgrade head
```

### 5. Bootstrap the first super-admin

```sh
.venv/bin/securgraph bootstrap-superadmin \
  --username admin \
  --display-name "Admin" \
  --password 'change-me-min-12-chars'
```

The password is shown once. Change it immediately after first login.

### 6. Start the API

```sh
make api
```

API runs on `http://localhost:8000`. Interactive docs at
`http://localhost:8000/docs`.

### 7. Start the web UI

```sh
npm --prefix web install
make web-dev
```

UI runs on `http://localhost:3000`. Sign in with the bootstrap account.

### 8. Verify health

```sh
curl -s http://localhost:8000/api/v1/health/live | python3 -m json.tool
curl -s http://localhost:8000/api/v1/health/ready | python3 -m json.tool
```

`/health/live` returns process status (zero cost). `/health/ready` returns
dependency checks + cached provider diagnostic (zero LLM completions).

### 9. Seed the MRD corpus (optional, for evaluation)

Dev corpus (`mrd_v1`):

```sh
make mrd-seed-live   # API should have SECURGRAPH_EVAL_BASELINE_TEMPLATES=1 for dev scoring
make mrd-live
```

Held-out corpus (`mrd_heldout_v1`) — **without** baseline templates on the API:

```sh
make heldout-seed-live
make heldout-live    # run twice; security metrics should match
```

Full reproduction from clean stack: [`docs/ARTIFACT_REPRODUCTION.md`](ARTIFACT_REPRODUCTION.md).

## MRD live scoring (dev)

```sh
make up && make migrate
# Terminal 1 — dev eval flags (baseline templates ON):
SECURGRAPH_AUTH_ENABLED=false SECURGRAPH_EVAL_IDENTITY_MODE=true \
  SECURGRAPH_EVAL_BASELINE_TEMPLATES=1 \
  SECURGRAPH_RATELIMIT_QUERY=0 SECURGRAPH_RATELIMIT_INGEST=0 make api
# Terminal 2:
make mrd-seed-live
make mrd-live
```

Archived reports are development evidence only. A release report must be generated from a clean worktree on the release commit, using `make heldout-live` twice with the mandated cloud providers and no baseline templates. The report separates six authorization-denial cases from two source-isolation cases; do not collapse them into one refusal metric.

## Production Checklist

- [ ] `SECURGRAPH_ENV=prod` in `.env`
- [ ] `SECURGRAPH_AUTH_ENABLED=true` (never disable in production; startup
      refuses `auth_enabled=false` in stage/prod)
- [ ] `AUDIT_HMAC_KEY` set to a strong random secret
- [ ] `SECURGRAPH_MASTER_KEY` set to a strong random secret
- [ ] `SECURGRAPH_AUDIT_ANCHOR_BUCKET`, endpoint, and dedicated credentials point to self-hosted MinIO Object Lock (`COMPLIANCE`, 365 days) on a separate host and administrative domain — Compose MinIO is integration-only
- [ ] `SECURGRAPH_FEATURE_WORKERS_ENABLED=true`; Redis and the ARQ worker are running for durable ingestion and periodic anchors
- [ ] Cloud LLM API keys configured via Settings UI (not plaintext in `.env`)
- [ ] Bootstrap super-admin password changed after first login
- [ ] PostgreSQL role is non-superuser without BYPASSRLS (RLS defense-in-depth)
- [ ] `SECURGRAPH_EVAL_IDENTITY_MODE` and `SECURGRAPH_EVAL_BASELINE_TEMPLATES` are NOT set (dev/test only)
- [ ] `SECURGRAPH_C3_ABLATION_DISABLE_PARTITION` is NOT set (unless running
      ablation study)

## Troubleshooting

### API won't start

Check `/tmp/securgraph-api.log` or the terminal output. Common causes:
- OpenFGA not migrated: `make up` runs the migrate container automatically
- PostgreSQL not healthy: `docker compose ps` and wait
- Missing API key: check `.env` for `MISTRAL_API_KEY` or `OPENAI_API_KEY`

### Query returns 500

- Check that OpenFGA store is bootstrapped (API log shows "Found existing
  OpenFGA store" or "created OpenFGA store")
- Check that the tenant exists and has FGA tuples seeded
- Check that the LLM provider is reachable

### Tamper chain reports `is_intact=false`

- Ensure `AUDIT_HMAC_KEY` is the same for writer and verifier (same `.env`)
- Ensure `started_at` is set explicitly on trace rows (not server_default)
- Run `.venv/bin/securgraph` backfill if legacy rows exist

### MinIO anchor fails

- Ensure MinIO bucket has Object Lock enabled (`mc mb --with-lock`)
- Ensure canonical `SECURGRAPH_AUDIT_ANCHOR_*` variables are set
- Ensure the production MinIO bucket has `COMPLIANCE` retention configured for 365 days

## Shutdown

```sh
make down          # stop containers, keep volumes
make nuke          # stop containers AND delete volumes (DESTRUCTIVE)
```
