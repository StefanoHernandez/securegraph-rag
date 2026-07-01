# SecureGraph RAG — Pilot Validation Report (D7)

**Version:** v0.1.0-rc1
**Date:** 2026-07-01
**Branch:** `feat/security-hardening`

## Executive Summary

SecureGraph RAG was validated against a production-like acceptance plan
spanning 9 phases. The framework demonstrates verifiable access correctness
(C1), cross-retriever consensus (C2), and policy-aware community detection
(C3) on a realistic 50-case QA suite with zero leakage.

## Validation Scope

| Area | Method | Result |
|---|---|---|
| **Security hardening (SGH-01..10)** | 15 regression tests + live concurrent fork test | 8 Done, 2 Partial |
| **PostgreSQL RLS** | Live integration test against migrated PostgreSQL | Pass |
| **S3 Object Lock anchoring** | Live test against MinIO with Object Lock compliance | Pass (24h retention verified) |
| **MRD corpus live scoring** | 50 QA pairs × 8 personas × 4 classification levels | Leakage=0%, Deny=100% |
| **Frontend i18n** | `check:i18n` gate + nested component localization | Pass (IT/EN) |
| **Playwright E2E** | 5 smoke tests (login, locale, dashboard) | Configured |

## Key Metrics

### C1 — Verifiable Access Correctness

| Metric | Value |
|---|---|
| Leakage rate | **0%** (0/50 QA pairs) |
| Deny accuracy | **100%** (13/13 deny cases produced hedge) |
| Tamper chain integrity | Verified intact under 4 concurrent writers |
| Merkle anchor | Object Lock COMPLIANCE mode, 24h retention |

### C2 — Cross-Retriever Consensus

| Metric | Value |
|---|---|
| NLI ensemble | 3-voter majority (asyncio.gather) |
| Consensus threshold | 0.6 (configurable) |
| High-trust gating | Authorized queries → high_trust=true; unauthorized → hedge |

### C3 — Policy-Aware Communities

| Metric | Value |
|---|---|
| No-leakage property | Proven by construction + Hypothesis property test |
| Ablation flag | `SECURGRAPH_C3_ABLATION_DISABLE_PARTITION` ready for delta measurement |

## Test Baseline

- **Unit tests:** 165 passed, 24 deselected (integration/live)
- **Integration tests:** RLS (1/1 pass), S3 anchor (3/3 pass, 1 skip)
- **Lint:** ruff check — all checks passed
- **Type:** mypy src — 0 issues in 78 source files
- **Frontend:** check:i18n OK, eslint clean, build compiled successfully

## Open Items (non-blocking for rc1)

- SGH-06: failure injection across all ingestion stores (partial)
- SGH-09: full E2E proof deferred (Playwright configured, live tests gated
  behind E2E_LIVE=1)
- Fase 4 (settings advanced): egress policy, cost estimate, reindex
  blue-green — deferred to backlog
- Citation generation: cloud LLM does not emit structured citations (30%
  expected-text match, 0% citation coverage — generation quality gap, not
  security)
- C3 ablation live run: flag implemented, delta measurement pending

## Conclusion

SecureGraph v0.1.0-rc1 is ready for release candidate tagging. The
zero-leakage result on the MRD corpus validates the C1 security claim. The
The engineering is
production-like with live-verified RLS, S3 Object Lock anchoring, and a
localized Forensic Vault UI.
