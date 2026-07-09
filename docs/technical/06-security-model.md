# 06 — Security Model

This document is the map from threats to mechanisms. SecureGraph's stance is **fail-closed everywhere**: authorization errors deny, store failures abort ingestion before dependent writes, misconfiguration refuses to boot.

## Threat model

Assets: document/chunk text and derived artifacts (graph edges, community summaries, answers); the audit trail itself; provider credentials.

| Adversary | Capability | Answered by |
| --- | --- | --- |
| Authenticated user over-reaching | Crafts queries to pull content beyond their grants (direct vector hits, graph hops through restricted docs, community summaries) | C1 per-object checks, path-cut, C3 partitioning |
| Cross-tenant caller | Valid session in tenant A probing tenant B | Session tenant binding, store-level tenant filters, PostgreSQL RLS + `NOBYPASSRLS` role |
| Client-side manipulation | Tampered browser state, forged `user_id` in request bodies | SGH-01: identity always derived server-side from the session; CSRF double-submit; body `user_id` ignored outside dev/test eval mode |
| Insider with DB write access | Rewrites the audit trail after an incident | HMAC chain (keyed, key not in DB) + Merkle anchors on S3 Object Lock (WORM) |
| Malicious document | Prompt injection via ingested content; oversized/hostile uploads | Documented trust boundary, strict grounding prompts, upload hardening |
| Server-side request forgery | Admin sets a provider `base_url` pointing at internal services | SSRF guard on egress URLs |
| Credential theft from disk/DB | Reads provider keys from PostgreSQL | AES-GCM encryption under `SECURGRAPH_MASTER_KEY`; secrets never returned by any API |
| Brute force / enumeration | Password guessing, login floods | Sliding-window rate limits (IP + username), timing-oracle-free login |

Out of scope (alpha): a compromised API host (holds the HMAC key in env), malicious super-admin, side channels through answer timing.

## C1 — Verifiable access correctness

The claim: *an auditor can re-derive, per answer, that every evidence object was authorized for the subject at query time.*

1. **Ground truth**: OpenFGA ReBAC model (`viewer` = owner ∪ group_viewer ∪ direct_viewer; chunks inherit from documents, communities from policy classes). Every retrieval-time check is per-object and fail-closed.
2. **Decision records**: each check writes a `PolicyDecisionRow` — object type/id, action, allow/deny, reason code (`openfga_ok`, `unauthorized_rbac`, `graph_path_cut`, `community_policy_class_ok`, …), engine, timestamp. Denies are recorded, not just allows.
3. **Tamper evidence**: with `SECURGRAPH_AUDIT_TAMPER_EVIDENT=true`, traces, evidence, and decisions are HMAC-SHA256-chained per tenant: canonical `key=val|…|prev_hash=<h>` message, key from `AUDIT_HMAC_KEY` (env only, never stored; unset ⇒ plain SHA-256 with a loud warning — dev only). Chain heads are `SELECT … FOR UPDATE`-serialized; `chain_sequence` is unique per tenant. Verification (`/admin/audit/verify-chain`, ledger-verify jobs) re-derives every hash and pinpoints tampered rows without cascading.
4. **External anchor**: periodically (≥10 000 new events or interval elapsed), a Merkle root over the chain heads is written to S3 with `ObjectLockMode=COMPLIANCE` and a retention date — the database alone can no longer rewrite history past an anchor. Local anchor storage refuses to run in prod.
5. **Replay**: `GET /explain/{trace_id}` reconstructs the answer's lineage as W3C PROV-style agents/activities/entities: per-evidence authorization outcomes with real snippets for allowed chunks (500-char cap), `CONFIDENTIAL - ACCESS DENIED` for denied ones, honest structural labels for graph paths (path text is not persisted), plus the consensus snapshot and chain status.
6. **Failures audit too**: failed queries persist a chained trace row; permission grant/revoke, job cancel (with pre-cancel status), and retries write `admin_audit_events`.

## C2 — Consensus as confidence

Security-relevant property: the confidence signal is computed from **independently authorized** evidence in two channels, and the synthesis behavior is decided in code (`direct` / `single_channel` / `cautious` / `no_evidence`) — an attacker cannot prompt the system into presenting single-channel or contradicted evidence as high-trust. Full mechanics in [04 — Query Pipeline](04-query-pipeline.md).

## C3 — Policy-partitioned communities

Community summaries are LLM-generated aggregates — the one derived artifact that could silently blend restricted and unrestricted content. C3 prevents this **structurally**:

1. Every chunk's access label (`owner + sorted group_viewers + sorted direct_viewers`) hashes to a 16-hex **policy-class key**; identical key ⟺ identical authorized-subject set.
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
| **Tenant egress policy** | Per-tenant rules (provider × stage × max classification) evaluated before any provider call; stored-but-invalid policy fails closed; blocked queries return HTTP 451 without egress. |
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

## Verification commands

```sh
make test-adversarial     # C3 no-leakage property (Hypothesis, fixed seed)
make test-sgh06           # fail-closed ingestion failure injection
make test-rls-live        # live RLS gate (requires make up + migrate)
make test-s3-anchor-live  # live S3 Object Lock anchor gate
make mrd-live             # 50-case allow/deny/leakage suite against a live stack
```
