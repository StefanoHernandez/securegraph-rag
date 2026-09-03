# 06 — Security Model

This document is the map from threats to mechanisms. SecureGraph's stance is **fail-closed everywhere**: authorization errors deny, store failures abort ingestion before dependent writes, misconfiguration refuses to boot.

## Threat model

Assets: document/chunk text and derived artifacts (graph edges, community summaries, answers); the audit trail itself; provider credentials.

| Adversary | Capability | Answered by |
| --- | --- | --- |
| Authenticated user over-reaching | Crafts queries to pull content beyond their grants (direct vector hits, graph hops through restricted docs, community summaries) | C1 per-object checks, path-cut, C3 partitioning |
| Cross-tenant caller | Valid session in tenant A probing tenant B | Session tenant binding, store-level tenant filters, PostgreSQL RLS + `NOBYPASSRLS` role |
| Client-side manipulation | Tampered browser state, forged `user_id` in request bodies | SGH-01: identity always derived server-side from the session; CSRF double-submit; body `user_id` ignored outside dev/test eval mode |
| Insider with DB write access | Rewrites the audit trail after an incident | HMAC chain (keyed, key not in DB) + Merkle anchors on self-hosted MinIO Object Lock (WORM) |
| Malicious document | Prompt injection via ingested content; oversized/hostile uploads | Documented trust boundary, strict grounding prompts, upload hardening |
| Server-side request forgery | Admin sets a provider `base_url` pointing at internal services | SSRF guard on egress URLs |
| Credential theft from disk/DB | Reads provider keys from PostgreSQL | AES-GCM encryption under `SECURGRAPH_MASTER_KEY`; secrets never returned by any API |
| Brute force / enumeration | Password guessing, login floods | Sliding-window rate limits (IP + username), timing-oracle-free login |

Out of scope (alpha): a compromised API host (holds the HMAC key in env), malicious super-admin, side channels through answer timing.

## C1 — Verifiable access correctness

The claim is limited to the persisted record: *an auditor can verify the recorded authorization decisions and evidence hashes for a query trace, and can re-check current policy state.* Historical OpenFGA tuple replay is not implemented.

1. **Ground truth**: OpenFGA ReBAC model (`viewer` = owner ∪ group_viewer ∪ direct_viewer; chunks inherit from documents, communities from policy classes). Every retrieval-time check is per-object and fail-closed.
2. **Decision records**: each check writes a `PolicyDecisionRow` — object type/id, action, allow/deny, reason code (`openfga_ok`, `unauthorized_rbac`, `graph_path_cut`, `community_policy_class_ok`, …), engine, timestamp. Denies are recorded, not just allows.
3. **Tamper evidence**: with `SECURGRAPH_AUDIT_TAMPER_EVIDENT=true`, traces, evidence, and decisions are HMAC-SHA256-chained per tenant: canonical `key=val|…|prev_hash=<h>` message, key from `AUDIT_HMAC_KEY` (env only, never stored; unset ⇒ plain SHA-256 with a loud warning — dev only). Chain heads are `SELECT … FOR UPDATE`-serialized; `chain_sequence` is unique per tenant. Verification (`/admin/audit/verify-chain`, ledger-verify jobs) re-derives every hash and pinpoints tampered rows without cascading.
4. **MinIO anchor**: periodically (>=10,000 new events or interval elapsed), a Merkle root over the chain heads is written to self-hosted MinIO with `ObjectLockMode=COMPLIANCE` and 365-day retention. This is an independent commitment only when MinIO is separately administered on a distinct host and domain; local or colocated MinIO is an integration boundary, not an external one. Local anchor storage refuses to run in prod.
5. **Replay**: `GET /explain/{trace_id}` reconstructs the answer's lineage as W3C PROV-style agents/activities/entities: per-evidence authorization outcomes with real snippets for allowed chunks (500-char cap), `CONFIDENTIAL - ACCESS DENIED` for denied ones, honest structural labels for graph paths (path text is not persisted), plus the consensus snapshot and chain status.
6. **Failures audit too**: failed queries persist a chained trace row; permission grant/revoke, job cancel (with pre-cancel status), and retries write `admin_audit_events`.

## C2 — Consensus as confidence

Security-relevant property: the confidence signal is computed from **independently authorized** evidence in two channels, and the synthesis behavior is decided in code (`direct` / `single_channel` / `cautious` / `no_evidence`) — an attacker cannot prompt the system into presenting single-channel or contradicted evidence as high-trust. Full mechanics in [04 — Query Pipeline](04-query-pipeline.md).

## C3 — Policy-partitioned communities

Community summaries are LLM-generated aggregates — the one derived artifact that could silently blend restricted and unrestricted content. C3 prevents this **structurally**:

1. Every chunk's access label (`owner + sorted group_viewers + sorted direct_viewers`) hashes to a 32-hex (128-bit) **policy-class key**; identical key ⟺ identical authorized-subject set.
2. The tenant graph's edges are partitioned by class **before** Louvain runs (per-class subgraphs; Level 0 resolution 1.2, Level 1 resolution 0.6, seed 42). Edges whose source chunks have no label go to a quarantined `unlabeled` bucket that receives no grants — invisible, fail-closed.
3. Retrieval does one FGA check per class (`viewer` on `policy_class:<key>`), then a per-community defense-in-depth check (`viewer` on `community:<id>`).
4. The guarantee is pinned by a Hypothesis property test (`tests/adversarial/test_c3_no_leakage.py`, 200 generated topologies): no bucket ever contains a cross-class edge.

Live ablation evidence (`eval/results/c3-ablation-live-20260701T154455Z.md`): partition ON → 14 classes, 89 communities, **0 mixed**; partition OFF → 1 class, 58 communities, **12 mixed** (structural leakage risk); retrieval-time checks still blocked the probe user in both runs — C3 removes the risk class, the FGA gate remains as the last line.

## Platform hardening

| Mechanism | Detail |
| --- | --- |
| **Startup guards** (fail-closed boot) | Default/short JWT secret → refuse in stage/prod; `SECURGRAPH_AUTH_ENABLED=false` → refuse in stage/prod; cloud provider paired with an Ollama-shaped model name (or vice versa) → refuse in stage/prod, warn in dev. |
| **Session security** | HttpOnly strict-SameSite cookies; access JWT 15 min; rotating refresh (+ CSRF rotation) with server-side hashed sessions; `token_version` bump invalidates all sessions on password/role change; `must_change_password` enforced server-side. |
| **CSRF** | Double-submit (cookie vs `x-csrf-token`, constant-time compare) + session-bound second check; login exempt; bearer calls skip. |
| **Rate limiting** | Sliding window per minute: login 10/IP + 5/username (reset on success), query 30, ingest 10. In-process — front with a shared limiter for multi-worker deployments. |
| **PostgreSQL RLS** | `FORCE ROW LEVEL SECURITY` + `tenant_isolation` policies on all tenant tables; API connects as `securgraph_app` (`NOSUPERUSER NOBYPASSRLS`, DML-only grants); GUCs set transaction-locally. Migrations use a separate privileged DSN. |
| **SSRF guard** | `validate_egress_url` on provider `base_url`s: http(s) only, resolves DNS and rejects private/loopback/link-local/multicast/reserved/unspecified addresses (blocks RFC-1918 and cloud metadata endpoints). Local exemption only for the `ollama` provider. |
| **Tenant egress policy** | Per-tenant rules (provider × stage) evaluated before any provider call; stored-but-invalid policy fails closed; blocked queries return HTTP 451 without egress. Rules do **not** filter by data classification — see "Egress and data classification" below. |
| **Credential storage** | Provider API keys AES-GCM-encrypted at rest under `SECURGRAPH_MASTER_KEY` (key versioning + fingerprint); settings endpoints return metadata only; encryption unavailable → 503, never plaintext fallback. |
| **Upload hardening** | Extension allowlist, 50 MB cap, filename sanitization, empty-file rejection, owner namespace/delegation checks. |
| **Eval identity mode** | `SECURGRAPH_EVAL_IDENTITY_MODE` (read-only authorization simulation for the eval harness) activates only when env ∈ {dev, test} AND auth is disabled — three independent conditions; a warning is logged per use. |
| **Fail-closed ingestion (SGH-06)** | Any store failure aborts before dependent stores write; verified by failure-injection tests (`make test-sgh06`). |

## Residual risks and known gaps

Tracked honestly rather than hidden:

- **In-process rate limiter** — per-process only; a multi-worker deployment needs a shared store.
- **HMAC key on the API host** — a compromised host can forge future chain entries (anchors bound the past).
- **Explain integrity status** — reports `sealed_unverified` without re-verifying the chain inline; full verification is a separate job.
- **No Neo4j schema constraints** — uniqueness relies on MERGE discipline.
- **Deterministic-synthesis templates** are benchmark-specific (see [09 — Evaluation](09-evaluation.md#methodology-caveats)); they can only emit retrieved authorized text, so they are not a leakage vector, but they are not a general mechanism either.
- **Anchor cadence** — events between anchors rely on DB integrity alone (bounded by the 10 000-event / interval thresholds).

## Environment-gated surface

Three things are available in dev and test and withdrawn in stage/prod,
because each is either reconnaissance or a mutation that a deployment must
perform deliberately rather than implicitly:

| Surface | dev / test | stage / prod |
| --- | --- | --- |
| `/docs`, `/redoc`, `/openapi.json` | served | disabled — they enumerate every route, parameter and model of a system whose claim is controlled access. The schema is in the repository for anyone who legitimately needs it. |
| Legacy unversioned route aliases (`/auth/login`, …) | mounted | not mounted — every path-keyed guard is written for `/api/v1` (see `_MUST_CHANGE_ALLOWED_PATHS`), so an unversioned copy doubles the surface those checks reason about |
| OpenFGA store / authorization-model auto-creation | bootstraps itself | **refuses to start** unless both ids are pinned |

The OpenFGA case is the one that matters most. Creating a store or writing an
authorization model decides who may read what. Performed implicitly at boot,
whichever process starts first defines authorization — and a brand-new store
denies everything until it is re-seeded, which looks exactly like a correctly
fail-closed system while actually being an outage. Deployments pin
`SECURGRAPH_OPENFGA_STORE_ID` and
`SECURGRAPH_OPENFGA_AUTHORIZATION_MODEL_ID` to what their provisioning step
created.

## Response headers

The web app and the API get different sets, because they serve different
content.

The web app (`web/next.config.ts`) sends a Content-Security-Policy locked to
same-origin: no external script, style, font, image, or frame host is
reachable and `connect-src` is `'self'` — the BFF proxies the API, so nothing
needs to leave the origin. `'unsafe-inline'` for styles and scripts is
unavoidable without per-request nonces, since Next injects inline bootstrap
scripts and style tags; the value of the policy here is the origin lock, not
inline protection. `frame-ancestors 'none'` blocks framing even where
`X-Frame-Options` is unsupported. Also sent: `X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, a `Permissions-Policy` denying camera,
microphone, geolocation, payment and USB (the UI uses none of them), and HSTS,
which is inert over plain HTTP and applies once TLS terminates in front.

The API sends `X-Content-Type-Options`, `X-Frame-Options` and
`Referrer-Policy`, plus `Cache-Control: no-store` on every endpoint that
mints, rotates or reveals session material — `/auth/login`, `/auth/refresh`,
`/auth/me`, `/auth/sessions`. It sends **no** CSP: the interactive docs load
Swagger UI from a CDN and a strict policy would break them.

`SECURGRAPH_ALLOWED_HOSTS` enables `TrustedHostMiddleware` against Host-header
poisoning and DNS rebinding. Empty (the default) accepts any host so local
development and container health checks keep working; set it in stage/prod.

## Egress and data classification

Egress rules match on **provider and stage only**. There is no classification
dimension, and the API rejects a rule that carries `max_classification`.

The field existed and was never enforced. `enforce_egress` is called from
`get_llm_adapter()` — at adapter *acquisition*, where the classification of
the evidence about to leave is not yet known — so the check ran against a
hardcoded `"internal"`. The consequences were worse than an absent control:

- a rule scoped to `internal` also released `confidential` and `restricted`
  evidence, because every level compared equal;
- a rule scoped to `public` never matched at all — silent dead configuration;
- the Settings UI offered exactly that shape (`openai / generation /
  max_classification: internal`) as its **default example**, so the first
  thing an operator saw promised a protection that did not exist.

Accepting the field and dropping it on the floor is the failure mode a
security control must not have, so the payload is now refused with an
explicit 400 rather than silently ignored.

### If the dimension is needed later

The framework already has the mechanism; it just is not wired to this policy.
`QueryScope.max_classification` means "ceiling by rank" and is honored by both
retrieval lanes — a Qdrant pre-filter in `_build_retrieval_filter`, and
`_apply_scope_to_chunks` on the graph lane. Enforcing egress by classification
therefore means:

1. derive the highest classification the configured `(provider, stage)` may
   receive from the tenant policy, and intersect it with the client-requested
   `scope.max_classification`;
2. cover the community/global lane, which has **no** scope filter today
   (`indexing/global_retriever.py`) — otherwise summaries bypass the ceiling;
3. keep a pre-call backstop that blocks and records a decision when evidence
   above the ceiling reaches a provider anyway, so the property stays
   auditable in the same way C1 is: *every chunk that reached a provider was
   cleared for that provider*. A filter alone proves only an absence;
4. decide the user-facing contract, because it changes: a ceiling **degrades**
   (answer from what may leave, with a `warnings` entry and a trace record)
   where today's policy **blocks** (HTTP 451). Both lanes must use the same
   ceiling, or the C2 agreement score measures a policy difference instead of
   a retrieval divergence.

With no policy configured `enforce_egress` returns early, so a ceiling derived
this way is inert on every path that does not set one — including the
held-out evaluation.

## Verification commands

```sh
make test-adversarial     # C3 no-leakage property (Hypothesis, fixed seed)
make test-sgh06           # fail-closed ingestion failure injection
make test-rls-live        # live RLS gate (requires make up + migrate)
make test-minio-anchor-live  # live MinIO Object Lock anchor gate
make mrd-live             # 50-case allow/deny/leakage suite against a live stack
```
