# SecureGraph RAG — Operator Deployment Guide (D8)

**Version:** v0.1.0-rc1

## Prerequisites

- **macOS** (Darwin 25.4+) or **Linux**
- **Docker Desktop** with ≥6 GB allocated
- **Python 3.11+**
- **[uv](https://docs.astral.sh/uv/)** (`brew install uv`)
- **Node.js 20+** and **npm**
- **Cloud LLM API key** (Mistral or OpenAI) — Ollama optional for future
  hardware

## Step-by-Step Deployment

### 1. Clone and configure environment

```sh
git clone <repo-url> SecureGraph
cd SecureGraph
cp .env.example .env
```

Edit `.env`:
- Set `SECURGRAPH_LLM_PROVIDER=mistral` (or `openai`)
- Set `SECURGRAPH_LLM_MODEL=mistral-small-latest`
- Set `MISTRAL_API_KEY` or `OPENAI_API_KEY`
- Set `SECURGRAPH_MASTER_KEY` to a long random secret (for encrypted
  provider credential storage)
- Set `AUDIT_HMAC_KEY` to a 32+ byte hex string (for tamper-evident audit
  chain)

### 2. Start backend containers

```sh
make up
```

This starts PostgreSQL, Qdrant, Neo4j, OpenFGA, Jaeger, and (with
`--profile anchor`) MinIO for S3 Object Lock anchoring.

To enable S3 Object Lock anchoring (recommended for production-like):

```sh
docker compose -f docker/docker-compose.yml --env-file .env --profile anchor up -d
```

Then set in `.env`:
```
SECURGRAPH_AUDIT_ANCHOR_S3_BUCKET=securgraph-audit-anchors
SECURGRAPH_AUDIT_ANCHOR_S3_ENDPOINT=http://localhost:9000
SECURGRAPH_AUDIT_ANCHOR_S3_ACCESS_KEY=minioadmin
SECURGRAPH_AUDIT_ANCHOR_S3_SECRET_KEY=minioadmin
```

For production AWS S3, set the bucket name and region, leave endpoint empty.

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

```sh
SECURGRAPH_EVAL_IDENTITY_MODE=true .venv/bin/python eval/mrd_seed_live.py --corpus corpus/mrd_v1
```

Then run live scoring:

```sh
.venv/bin/python eval/mrd_qa_smoke.py \
  --live-query-url http://localhost:8000/api/v1/query \
  --tenant-id mrd-lab \
  --output-dir eval/results
```

## MRD live scoring

```sh
make up && make migrate
# Terminal 1 — disable rate limits for batch seed/scoring:
SECURGRAPH_AUTH_ENABLED=false SECURGRAPH_EVAL_IDENTITY_MODE=true \
  SECURGRAPH_RATELIMIT_QUERY=0 SECURGRAPH_RATELIMIT_INGEST=0 make api
# Terminal 2:
make mrd-seed-live
make mrd-live
```

Latest report: `eval/results/mrd-qa-smoke-20260709T145548Z.*` (31/50 pass, 0 leakage, 13/13 deny).

## Production Checklist

- [ ] `SECURGRAPH_ENV=prod` in `.env`
- [ ] `SECURGRAPH_AUTH_ENABLED=true` (never disable in production; startup
      refuses `auth_enabled=false` in stage/prod)
- [ ] `AUDIT_HMAC_KEY` set to a strong random secret
- [ ] `SECURGRAPH_MASTER_KEY` set to a strong random secret
- [ ] `SECURGRAPH_AUDIT_ANCHOR_S3_BUCKET` set to a real S3 Object Lock bucket
- [ ] Cloud LLM API keys configured via Settings UI (not plaintext in `.env`)
- [ ] Bootstrap super-admin password changed after first login
- [ ] PostgreSQL role is non-superuser without BYPASSRLS (RLS defense-in-depth)
- [ ] `SECURGRAPH_EVAL_IDENTITY_MODE` is NOT set (dev/test only)
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

### S3 anchor fails

- Ensure MinIO bucket has Object Lock enabled (`mc mb --with-lock`)
- Ensure `SECURGRAPH_AUDIT_ANCHOR_S3_*` env vars are set
- In production, ensure the S3 bucket has Object Lock retention configured

## Shutdown

```sh
make down          # stop containers, keep volumes
make nuke          # stop containers AND delete volumes (DESTRUCTIVE)
```
