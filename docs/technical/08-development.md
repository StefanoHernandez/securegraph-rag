# 08 — Development Guide

How to set up a working environment, run the quality gates, and contribute changes.

## Prerequisites

- macOS (tested on Darwin 25.x) or Linux
- Docker Desktop with ≥ 6 GB allocated
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+ and npm (web UI)

## Environment setup

```sh
cp .env.example .env        # never commit .env — it holds secrets
make venv                   # creates .venv via `uv sync --frozen` from uv.lock
make up                     # starts postgres, qdrant, neo4j, openfga, jaeger (+ redis with --profile workers)
make migrate                # alembic upgrade head (uses the superuser/migration DSN)
.venv/bin/securgraph bootstrap-superadmin --username admin --display-name "Admin" --password '<min-12-chars>'
make api                    # uvicorn on :8000 (reload)
npm --prefix web install && make web-dev   # Next.js on :3000
```

All Python commands go through `.venv/bin/…` — never install project dependencies globally. Dependencies are declared in `pyproject.toml` and locked in `uv.lock` (tracked): `make venv` and `make sync` install with `uv sync --frozen`, so local verification is reproducible. After editing `pyproject.toml`, regenerate the lock (`uv lock`) and commit it with the change. The eval harness keeps its own lockfile under `eval/`.

The API connects as the least-privilege PostgreSQL role `securgraph_app` (granted by migration `f6a7b8c9d0e1`); migrations run under the superuser DSN (`SECURGRAPH_POSTGRES_MIGRATE_DSN`). Keep the two DSNs distinct — row-level security is only meaningful when the app role cannot bypass it.

## Make targets

| Target | Purpose |
| --- | --- |
| `make venv` / `make sync` | Create / refresh the virtualenv from `uv.lock` (frozen). |
| `make up` / `make down` / `make nuke` | Start / stop / stop-and-wipe backend containers (`nuke` deletes volumes — destructive). |
| `make api` | Run FastAPI with reload on `:8000`. |
| `make migrate` | Apply Alembic migrations to head. |
| `make seed` | Seed dev-only demo users. |
| `make fmt` / `make lint` / `make type` | ruff format / ruff check / mypy on `src`. |
| `make test-unit` | Unit tests only — Docker-free and network-free (hermetic). |
| `make test` | Unit + integration (requires `make up`). |
| `make test-adversarial` | Hypothesis property tests (C3 no-leakage gate), fixed seed. |
| `make test-rls-live` / `make test-minio-anchor-live` / `make test-live-gates` | Release-blocking live gates: PostgreSQL RLS and MinIO Object Lock anchoring against real backends. |
| `make test-sgh06` | Cross-store failure-injection bundle (fail-closed ingestion). |
| `make test-ingest-chaos-live` | Live ingest reconcile/worker chaos tests. |
| `make mrd-seed-live` / `make mrd-live` | Seed the MRD corpus into a live stack / run the 50-case MRD QA smoke against `/query` (writes `eval/results/`). |
| `make ci` | Docker-free local aggregate: `lint + type + test-unit`. |
| `make cov` | Docker-free unit/adversarial branch-coverage gate, minimum 70% (terminal + HTML). |
| `make web-lint` / `make web-i18n` / `make web-build` / `make web-e2e` | Frontend gates (Playwright E2E needs API + web running). |
| `make eval-venv` / `make eval` / `make baseline` | Isolated eval environment and evaluation runs (see [09 — Evaluation](09-evaluation.md)). |

## Test taxonomy

Markers are declared in `pyproject.toml` and enforced by `make` targets:

| Marker | Contract | Local release gate? |
| --- | --- | --- |
| `unit` | No external services, no network. Everything is mocked, including DNS lookups made by the SSRF guard. Must pass offline. | Yes |
| `integration` | Requires live Docker backends (`make up`). | Manual |
| `adversarial` | Hypothesis property tests pinning security invariants (C3 no-leakage). | Yes, before release |
| `live` | Real LLM/network calls. Explicit opt-in. | Manual |
| `slow` | Long-running. Explicit opt-in. | Manual |

Layout: `tests/unit/` (route-level security, workflow logic, domain), `tests/integration/` (real stores, consensus, tamper chain), `tests/adversarial/` (C3 property test).

Conventions worth knowing before writing tests:

- `tests/conftest.py` sets `SECURGRAPH_ENV=test`; unit tests construct `Settings(...)` explicitly. Settings fields use validation aliases — pass the *env-var name* as the kwarg (e.g. `Settings(JWT_SECRET="…")`), and pass every `llm_model*` kwarg explicitly so values from a developer's `.env` cannot leak into assertions.
- Route tests use the FastAPI dependency-override pattern already present in `tests/unit/test_query_route.py` and `test_admin.py`.

## Local verification

SecureGraph does not run GitHub Actions. Before a release or manual review,
run the following local verification gates:

- **backend** — Python 3.11 provisioned with `uv sync --frozen` from `uv.lock`: `make lint`, `make type`, `make test-unit`, and `make cov` (70% minimum). The unit suite and coverage gate are intentionally Docker-free; do not switch the job to `make test`.
- **web** — Node 20: `npm run lint`, `npm run check:i18n`, `npm run build` (the build runs the typed-routes typegen, which covers `tsc`).

Run `make ci && make cov` locally for the backend baseline.

## Code conventions

- **Python**: ruff (format + lint) and mypy are gating; `src/` is fully typed. SQLAlchemy 2 async style; Pydantic v2 models as the domain lingua franca; adapters implement `Protocol` interfaces from `domain/`.
- **API**: all routes live under `/api/v1`; every mutating admin action writes an `AdminAuditEventRow`; new endpoints need route-level tests covering the auth failure path, not just the happy path.
- **Frontend**: Next.js App Router, strict TypeScript, `next-intl` with `/it` and `/en` locales — `npm run check:i18n` fails on missing keys. No client-side authorization decisions: the UI displays what the backend returns, it never simulates access checks.
- **Migrations**: one Alembic head at all times; check with `.venv/bin/alembic heads` before committing a migration.
- **Secrets**: `.env` is gitignored and must stay out of commits; tests and docs must never embed real key material.

## Contributing a change

1. Branch from `main` (`feat/…` or `fix/…`).
2. Implement with tests: unit for logic, integration when a real store matters, adversarial when a security invariant moves.
3. Gate locally: `make ci && make cov` (backend) and `make web-lint && make web-i18n && make web-build` when the UI changed.
4. If retrieval, policy, or synthesis behavior changed, re-run the MRD evaluation with `make mrd-live` ([09 — Evaluation](09-evaluation.md)) — the leakage-zero and deny invariants are release-blocking.
5. Open a PR to `main` after recording the applicable local gate results.

## Known lint debt

`react-hooks/set-state-in-effect` is downgraded to a warning in
`web/eslint.config.mjs`. It arrived with next 16.3 and fires on six
pre-existing effects that synchronise local state with an external value:
three in `app/(dashboard)/ingest/page.tsx`, two in
`app/(dashboard)/vectors/page.tsx`, and `useSessionUser` in `lib/session.ts`.

They are a cascading-render performance concern, not correctness or security.
Fixing them properly means reworking how those pages own their state —
deriving instead of synchronising, and `useSyncExternalStore` with a cached
snapshot for `useSessionUser` — and none of those pages currently has test
coverage, so the change would be unverifiable. Downgraded rather than
disabled so the warnings stay on screen.

Whoever picks this up: add coverage for the ingest and vectors flows first,
then restore the rule to `error`.

## Troubleshooting quick hits

| Symptom | Likely cause / fix |
| --- | --- |
| `make test-unit` fails offline | A test is hitting real network — mock it; the suite's contract is hermetic. |
| Upload returns 503 "Retry requires async ingestion workers" | `SECURGRAPH_FEATURE_WORKERS_ENABLED=true` but Redis is not running; start the `workers` compose profile or unset the flag (sync ingestion needs no Redis). |
| Startup `RuntimeError` about provider/model coherence | A cloud `llm_provider` is configured with an Ollama-shaped model name (or vice versa); align `SECURGRAPH_LLM_PROVIDER` and `SECURGRAPH_LLM_MODEL*`. |
| 429 on login/query/ingest during manual testing | In-process sliding-window rate limits; wait or tune the `SECURGRAPH_RATELIMIT_*` settings in dev. |
| Frontend `ENVIRONMENT_FALLBACK` log during build | Known non-fatal prerender warning. |
