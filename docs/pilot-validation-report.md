# SecureGraph RAG - Archived Pilot Validation Report (D7)

**Status:** Historical pilot record; not submission or release evidence
**Pilot version:** v0.1.0-rc1
**Pilot date:** 2026-07-01
**Pilot branch:** `feat/security-hardening`

## Purpose and limits

This document records the 1 July 2026 pilot exactly enough to explain the
project history. It does **not** certify the current submission-ready release.
The pilot predates the current deny scorer, heldout-corpus isolation,
audit-payload v2, automatic anchor jobs, and the 365-day MinIO deployment
requirements.

In particular, a pilot deny was counted when the response used a hedge. The
current gate requires an explicit refusal, no forbidden source, and no
forbidden protected claim. Consequently the historical "Deny=100%" result
below must not be read as a zero-leakage claim under the current protocol.

The MinIO test below was a local integration test using Object Lock
`COMPLIANCE` with 24-hour retention. It does not demonstrate the current
production requirement: self-hosted MinIO Object Lock in a separate failure
domain and administrative domain, with 365-day `COMPLIANCE` retention.

## Historical Pilot Scope

| Area | Method used in the pilot | Historical result |
|---|---|---|
| Security hardening (SGH-01..10) | Regression tests, failure injection, live concurrent fork test | 9 done, 1 partial |
| PostgreSQL RLS | Live integration test against migrated PostgreSQL | Pass |
| MinIO Object Lock anchoring | Local live integration test | Pass, 24-hour retention verified |
| MRD corpus live scoring | 50 QA pairs x 8 personas x 4 classifications | Old scorer reported leakage=0%, deny=100% |
| Settings advanced | Egress policy, token/cost estimate, blue-green reindex plan | Implemented after rc1 |
| Frontend i18n | `check:i18n` gate and nested component localization | Pass, IT/EN |
| Playwright E2E | Five live smoke tests: login, invalid login, locale, dashboard | Pass |

## Historical Measurements

### C1 - Verifiable Access Correctness

| Measurement | Pilot value | Current interpretation |
|---|---|---|
| Leakage rate | 0% over 50 QA pairs | Requires remeasurement with the current scorer and heldout corpus |
| Deny accuracy | 13/13 deny cases produced a hedge | Not equivalent to the current explicit-refusal security gate |
| Citation source coverage | 36/37 allow cases in the pre-fix live smoke; `mrd-45` retry cited `file://14_repository_readme.md` | Historical answer-quality observation only |
| Tamper chain integrity | Intact under four concurrent writers | Superseded by audit payload v2 verification requirements |
| Merkle anchor | Object Lock `COMPLIANCE`, 24-hour retention | Superseded by the 365-day MinIO production policy |

### C2 - Cross-Retriever Consensus

| Measurement | Pilot value | Current interpretation |
|---|---|---|
| NLI ensemble | Three-voter majority via `asyncio.gather` | Requires calibration and lane ablation reports |
| Consensus threshold | 0.6, configurable | Requires threshold-sensitivity evidence |
| High-trust gating | Authorized queries high-trust; unauthorized queries hedge | Requires the current deny scorer |

### C3 - Policy-Aware Communities

| Measurement | Pilot value | Current interpretation |
|---|---|---|
| No-leakage property | Construction argument and Hypothesis property test | Current implementation uses canonical 128-bit policy-class digests |
| Live ablation | C3 on: 14 classes, 89 communities, 0 mixed. C3 off: 1 class, 58 communities, 12 mixed. Restored: 14 classes, 89 communities, 0 mixed. | Historical evidence; rerun required for release evidence |
| Evidence | `eval/results/c3-ablation-live-20260701T154455Z.json` | Historical artifact only |

## Historical Test Baseline

- Unit tests: 165 passed, 24 deselected (integration/live).
- Integration tests: RLS 1/1 pass; MinIO anchor 3/3 pass, 1 skipped.
- Static checks: Ruff passed; mypy reported 0 issues across 78 source files.
- Frontend: `check:i18n`, ESLint, and production build passed.

These counts describe the pilot checkout and must not be compared directly to
the current test suite.

## Supersession

The current acceptance contract is in
[ARTIFACT_REPRODUCTION.md](../ARTIFACT_REPRODUCTION.md) and the technical
evaluation documentation. A release is blocked until all of the following are
green on the current commit:

- clean heldout execution with two independently reproducible runs;
- zero forbidden-source and forbidden-claim leaks under the explicit-refusal
  scorer;
- audit v1/v2 verification, scheduled MinIO Object Lock anchors, and durable
  ingest-saga reconciliation;
- calibrated C2 reports with lane ablation and C3 evidence;
- backend and frontend static checks, test gates, and live integration gates.

## Archived Conclusion

The pilot demonstrated useful early integration coverage and informed the
submission-ready remediation work. It is not a basis for release tagging,
published claims, or production-security conclusions.
