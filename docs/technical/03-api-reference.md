# 03 — API Reference

All endpoints are served by FastAPI under the versioned prefix **`/api/v1`** (`src/securgraph/api/main.py`). Un-versioned aliases exist for alpha-migration compatibility but are excluded from OpenAPI; clients must use `/api/v1`. Interactive docs: `http://localhost:8000/docs`.

Conventions used below: paths omit the `/api/v1` prefix; "admin" = `super_admin` or `tenant_admin` role; request-body validation failures return the standard FastAPI **422**.

## Authentication

Sessions are cookie-based; the browser never holds a bearer token.

| Cookie | HttpOnly | Purpose |
| --- | --- | --- |
| `securgraph_access` | yes | JWT (HS256) access token — claims `sub`, `username`, `role`, `tenant_id`, `sid`, `token_version`; TTL 15 min (configurable 5–60). |
| `securgraph_refresh` | yes | Opaque rotating refresh token (stored server-side as SHA-256 hash); TTL 7 days (1–90). |
| `securgraph_csrf` | no (JS-readable) | CSRF double-submit token bound to the session. |

Mechanics worth knowing:

- **CSRF**: unsafe methods (`POST/PUT/PATCH/DELETE`) from cookie-authenticated callers must send `x-csrf-token` matching the `securgraph_csrf` cookie (compared with `hmac.compare_digest`); failure → 403. `/auth/login` is exempt. A second, session-bound CSRF check runs in the auth dependency. Bearer-authenticated requests skip CSRF.
- **Bearer fallback**: `Authorization: Bearer <jwt>` is accepted as an alternative to the access cookie (service-to-service, eval harnesses).
- **Refresh rotation**: `POST /auth/refresh` rotates *both* the refresh and CSRF tokens and reissues the access token; an invalid refresh clears cookies and returns 401.
- **`token_version`**: bumped on password change, role change, or admin reset — instantly invalidates every outstanding session for that user.
- **`must_change_password`**: enforced server-side; a flagged user can only call `/auth/change-password`, `/auth/me`, `/auth/logout`, `/auth/refresh` (403 elsewhere).
- **Roles**: `super_admin`, `tenant_admin`, `analyst`, `viewer`. Tenant scope is enforced from membership rows, never from client input.
- `SECURGRAPH_AUTH_ENABLED=false` is a dev/test convenience: dependencies return no principal and routes fall back to the `user:anonymous` subject. Startup **refuses to boot** with auth disabled in `stage`/`prod`.

## Rate limiting

In-process sliding window, 60-second window, per `(scope, key)`; exceeding a limit returns **429** with `Retry-After: 60`. Disabled when `env=test` or a limit is set ≤ 0. Single-process only — front it with a shared limiter when running multiple workers.

| Scope | Default (req/min) | Key | Setting |
| --- | --- | --- | --- |
| Login per IP | 10 | client IP | `SECURGRAPH_RATELIMIT_LOGIN_IP` |
| Login per user | 5 | username (reset on success) | `SECURGRAPH_RATELIMIT_LOGIN_USER` |
| Query | 30 | user id | `SECURGRAPH_RATELIMIT_QUERY` |
| Ingest | 10 | user id | `SECURGRAPH_RATELIMIT_INGEST` |

## Endpoint catalog

### Session (`/auth`)

| Method | Path | Auth | Purpose | Errors |
| --- | --- | --- | --- | --- |
| POST | `/auth/login` | public | Authenticate; sets the three session cookies. Body `{username, password, tenant_id?}`. | 401, 403 (no active membership), 429 |
| POST | `/auth/refresh` | refresh cookie | Rotate refresh + CSRF, reissue access. | 401 |
| POST | `/auth/logout` | session | Revoke current session, clear cookies. | 401 |
| GET | `/auth/me` | session | Current user profile + tenant memberships. | 401 |
| POST | `/auth/change-password` | session | Body `{current_password, new_password}` (min 12 chars); bumps `token_version`, revokes other sessions. | 400, 401 |
| GET | `/auth/sessions` | session | List active sessions. | 401 |
| DELETE | `/auth/sessions/{session_id}` | session | Revoke one session. | 401, 404 |
| POST | `/auth/switch-tenant` | session | Switch active tenant (membership required); rotates tokens. | 401, 403 |

### Query, explain

| Method | Path | Auth | Purpose | Errors |
| --- | --- | --- | --- | --- |
| POST | `/query` | user | Run the LangGraph hybrid RAG workflow. Body `{query, tenant_id, user_id, options{retrieval_mode, top_k, consensus_threshold, …}}`. Returns `{answer, citations, confidence, channels, trace_id, warnings}`. The effective subject is always derived from the session (`user_id` in the body is honored only under the dev/test eval-identity mode). Persists the query trace + retrieval evidence; tamper-chains it when `SECURGRAPH_AUDIT_TAMPER_EVIDENT=true`. Failed queries persist a trace too. | 403 (tenant mismatch), 429, 451 (tenant egress policy blocked the provider call), 500 |
| GET | `/explain/{trace_id}?tenant_id=…` | user (trace owner or admin) | W3C PROV-style lineage report: agents/activities/entities, per-evidence authorization decisions with real chunk snippets (500-char cap), consensus, integrity status. Denied evidence is redacted as `CONFIDENTIAL - ACCESS DENIED`. | 403, 404 |

### Ingestion

| Method | Path | Auth | Purpose | Errors |
| --- | --- | --- | --- | --- |
| POST | `/ingest` | user | Multipart upload: `file`, `tenant_id`, `owner_id?`, `document_type`, `classification`, `policy_tags`, `title_override?`. Extensions: `.md .markdown .txt .pdf .docx .xlsx`; 50 MB cap. Sync path returns **201** with `{document_id, chunks_created, trace_id}`; with `SECURGRAPH_FEATURE_WORKERS_ENABLED=true` returns **202** and enqueues an arq job (source retained AES-GCM-encrypted for retry). | 400 (extension/empty/size/owner), 403, 429, 503 (workers flag on but Redis down; encryption unavailable) |
| GET | `/ingest/status/{job_id}` | user | Ingestion job status. | 403, 404 |

### Health (`meta`)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/health/live` | public | Zero-cost liveness: `{status, service, version}`. |
| GET | `/health/ready` | user | Readiness with per-component pings (`postgres`, `qdrant`, `neo4j`, `openfga`) + cached provider diagnostic. `status` is `down` if any datastore is unavailable; the LLM provider is never pinged here. |
| GET | `/health` | user | **Deprecated** alias of `/health/ready`. |
| POST | `/admin/diagnostics/providers/{provider}` | super-admin | Active LLM-provider diagnostic (15-min cache). 400 if provider not active. |

### Operations (tenant admin)

All under `/tenants/{tenant_id}/…`, gated on tenant-admin for that tenant (403 otherwise). Job launches return **202** with a job summary; 503 when the underlying store/worker is unavailable.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `…/ingest-jobs` | List ingestion jobs (`status`, `limit`). |
| GET | `…/operation-jobs` | List operation jobs (`kind`, `limit`). |
| POST | `…/operation-jobs/ledger-verify` | Verify the tamper chain. |
| POST | `…/operation-jobs/ledger-anchor` | Write a Merkle root anchor. |
| POST | `…/operation-jobs/ledger-anchor-due` | Anchor only if the schedule is due. |
| POST | `…/operation-jobs/community-recompute` | Recompute C3 communities. |
| POST | `…/operation-jobs/eval-baseline` | Run the baseline eval harness (400 on bad corpus path). |
| POST | `…/ingest-jobs/{job_id}/retry` | Re-enqueue a failed job from its encrypted source (404, 409 not-failed/no-source, 503 workers off). |
| POST | `…/ingest-jobs/reconcile` | Mark stale `running` jobs (>10 min) as failed. |
| POST | `…/operation-jobs/{job_id}/cancel` | Cancel a non-terminal job (409 if terminal). Audited with the pre-cancel status. |

### Admin (`/admin`)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/admin/audit/traces` | admin (tenant-scoped) | Paginated query traces. |
| POST | `/admin/audit/verify-chain` | admin (tenant-scoped) | Recompute and verify the HMAC chain for a tenant. |
| POST | `/admin/audit/backfill-chain` | super-admin | Seal legacy unsealed rows (409 if a chain already exists). |
| GET | `/admin/permissions` | super-admin | List OpenFGA tuples. |
| POST | `/admin/permissions/grant` | super-admin | Write a tuple `{user, relation, object}`. Audited (`AdminAuditEventRow`). |
| POST | `/admin/permissions/revoke` | super-admin | Delete a tuple. Audited. |
| GET | `/admin/graph/nodes` | admin (tenant-scoped) | Export Neo4j entities/edges. |
| GET | `/admin/graph/communities` | admin (tenant-scoped) | Community summaries. |

### Control plane (identities, groups, templates)

Tenant-admin gated unless noted (super-admin bypasses tenant checks).

| Method | Path | Purpose | Notable errors |
| --- | --- | --- | --- |
| POST | `/tenants` | Create tenant + seed business groups (**super-admin**). | 409 id exists |
| GET | `/tenants` | List tenants visible to the caller. | |
| PATCH | `/tenants/{id}` | Update display/status/settings-delegation flag (**super-admin**). | 404 |
| GET | `/users/search?q=…` | Search users (admin). | 403 |
| GET/POST | `/tenants/{id}/users` | List / create users (temp password returned once). | 403 creating super_admin, 409 username |
| POST | `/tenants/{id}/users/{uid}/membership` | Attach an existing user. | 404 |
| PATCH | `/tenants/{id}/users/{uid}` | Update role / active flag. | 404 |
| POST | `/tenants/{id}/users/{uid}/reset-password` | Reset password, revoke sessions. | 404 |
| GET/POST | `/tenants/{id}/groups` | List / create groups. | 409 slug |
| PATCH | `/tenants/{id}/groups/{gid}` | Update group. | 404 |
| PUT/DELETE | `/tenants/{id}/groups/{gid}/members/{uid}` | Add / remove member (204). | 404 |
| GET | `/tenants/{id}/groups/{gid}/members` | List members. | 404 |
| GET/POST | `/tenants/{id}/access-templates` | List / create access templates. | 409 duplicate type |
| PATCH | `/tenants/{id}/access-templates/{tid}` | Update template (bumps version). | 404 |

### Settings (providers, models, runtime)

Gated by the settings-admin rule: super-admin always; tenant-admin only when the tenant's `tenant_admin_settings_enabled` flag is on (403 "Settings delegation is disabled" otherwise); 404 for missing/archived tenants.

| Method | Path | Purpose | Notable errors |
| --- | --- | --- | --- |
| GET | `…/settings/providers` | List provider credentials (metadata only — secrets never returned). | |
| POST | `…/settings/providers/{provider}/credential` | Upsert an API key: SSRF-validated `base_url`, live verification, model discovery; stored AES-GCM-encrypted under `SECURGRAPH_MASTER_KEY`. | 400 bad URL / verify failed, 503 encryption unavailable |
| POST | `…/settings/providers/{provider}/credential/test` | Re-verify a stored credential and refresh its status/model catalog. | 400, 404 |
| DELETE | `…/settings/providers/{provider}/credential` | Remove a stored credential (204). | 404 |
| GET | `…/settings/models` | Discovered model catalog. | |
| PUT | `…/settings/runtime/{key}` | Upsert a runtime setting. Allowed keys: `llm_provider`, `llm_model`, `llm_provider_extract`, `llm_model_extract`, `llm_provider_judge`, `llm_model_judge`, `fallback_policy` (`block` \| `default_provider`), `hard_token_cap` (256–131072), `egress_policy`, `cost_caps`, `embedding_reindex_plan`. `embedding_model`, `storage_backend`, `auth_enabled` require a restart and are not runtime-settable. | 400 validation |
| POST | `…/settings/cost-estimate` | Token/cost estimate + egress decision for a prospective call. | 400 egress blocked |
| POST | `…/settings/embedding-reindex/plan` | Persist a deterministic blue-green reindex plan (201). | |

### Config

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/config` | user | Active non-sensitive runtime config (providers, models, embedding, `auth_enabled`, `audit_tamper_evident`). Never returns secrets. |

## Error semantics summary

| Status | Meaning in this API |
| --- | --- |
| 400 | Domain validation: bad upload, bad URL, bad runtime key/value, bad corpus path. |
| 401 | Missing/expired/invalid session or refresh token. |
| 403 | Role/tenant/CSRF/delegation failure; `must_change_password` block. |
| 404 | Resource or tenant not found (also used to avoid existence leaks). |
| 409 | Conflict: duplicate id/username/slug, retry on non-failed job, chain already sealed. |
| 422 | Pydantic request validation (FastAPI default). |
| 429 | Rate limit exceeded (`Retry-After: 60`). |
| 451 | Query blocked by the tenant egress policy (no provider call was made). |
| 503 | Dependency unavailable: Redis with workers enabled, encryption master key missing, store down during a job launch. |

## Startup guards (fail-closed boot)

The API refuses to start (RuntimeError in `stage`/`prod`; warning in dev) when:

- the JWT secret is the default or shorter than 32 chars (`enforce_secret_hygiene`);
- authentication is disabled (`enforce_auth_hygiene`) — stage/prod must never boot with `SECURGRAPH_AUTH_ENABLED=false`;
- a cloud provider (`openai`/`mistral`/`anthropic`/`google`) is paired with an Ollama-shaped model name, for any of the generation/extract/judge stages (`check_provider_model_coherence`).
