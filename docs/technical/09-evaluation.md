# 09 — Evaluation

SecureGraph's evaluation centers on the **MRD corpus** (`corpus/mrd_v1`), a pseudonymized but realistic multi-tenant document set with a hand-labeled allow/deny QA suite, plus a **held-out** corpus (`corpus/mrd_heldout_v1`) frozen separately from dev tuning. The security invariants — zero forbidden-source leaks, zero forbidden-claim leaks, and correct denials — are release gates; answer quality is tracked but interpreted with the caveats below.

## The MRD dev corpus

| Dimension | Value |
| --- | --- |
| Documents | 17 (policies, contracts, project charters, minutes, timesheets, README) — also generated as PDF/DOCX/XLSX artifacts |
| Classifications | restricted 4 · confidential 9 · internal 3 · public 1 |
| Groups | 8 (direction, administration, senior-management, project-managers, senior-technicians, project-aurora, project-borealis, consultants) |
| Document owners | 3 (`mrd.director`, `mrd.pm`, `mrd.senior-tech`) |
| Query personas | 8 (`mrd.director`, `mrd.pm`, `mrd.senior-tech`, `mrd.junior-tech`, `mrd.consultant`, `mrd.admin`, `mrd.external`, `mrd.superadmin`) |
| QA pairs | 50 — **37 allow** (persona is authorized; golden answer + required sources) / **13 deny** (persona must get a refusal; forbidden sources listed) |

Access is declared in `corpus/mrd_v1/manifest.yaml` (owner, groups, direct viewers, classification per document) and enforced through real OpenFGA tuples at seeding time — the eval exercises the production authorization path, not a mock.

## Held-out corpus (`mrd_heldout_v1`)

| Dimension | Value |
| --- | --- |
| Tenant | `helios-orion-lab` (Helios/Orion personas — no MRD canaries) |
| QA pairs | 20 — 12 allow / 6 `authorization_denial` / 2 `source_isolation` |
| Tuning policy | **Never** used for workflow/scorer changes after freeze |
| API flag | `SECURGRAPH_EVAL_BASELINE_TEMPLATES` must be **unset** (or `0`) for held-out runs |

Two consecutive live runs (2026-07-10) reported **0** forbidden-source leaks, **0** forbidden-claim leaks, and **8/8** security cases pass. Allow quality: **19/20**.

## Scoring (deny gate v2)

`eval/mrd_qa_smoke.py` + `src/securgraph/corpus/qa.py`:

- **Forbidden-source leak** (exact): a deny or source-isolation case fails if any forbidden source appears in citations. Gate: **0**.
- **Forbidden-claim leak** (exact): restricted fact appears in the answer body (canary phrases / `forbidden_claims` in QA YAML).
- **Deny pass rule** (`authorization_denial`): `refusal_detected ∧ ¬forbidden_source_leak ∧ ¬forbidden_claim_leak` — **never LLM-judged**.
- **Source-isolation pass rule**: `¬forbidden_source_leak` only.
- **Required sources** (exact): allow cases must cite their required source URIs.
- **Expected text** (lenient): normalized containment with key-fact fallbacks — tracked separately from security.

Each run JSON includes a **manifest**: git commit, dirty flag, corpus SHA-256, models, seed, and `results_sha256`.

### Semantic judge (secondary only)

Optional flag `--semantic-judge` calls OpenAI `gpt-4o-mini` to score semantic leakage on deny cases (`semantic_leak_score`). This is a **secondary diagnostic metric only** — it is never used as a pass/fail gate. Generation/extraction uses Mistral; the judge is intentionally independent (see P1.4 in the RAGAS baseline docs).

## Current results (dev)

Live runs against the full stack (cloud LLM provider, real Postgres/Qdrant/Neo4j/OpenFGA), 50 queries per run:

| Date (commit) | Passed | Allow | Deny (v2) | Leaks | What changed |
| --- | --- | --- | --- | --- | --- |
| 2026-07-01 | 21/50 | 8/37 | 13/13 | **0** | Baseline: exact-substring scorer |
| 2026-07-04 | 31/50 | 18/37 | 13/13 | **0** | `decide_synthesis` + normalized scorer |
| 2026-07-09 | 31/50 | 18/37 | 13/13 | **0** | Cloud-API confirmation |
| 2026-07-09 | 42/50 | 29/37 | 13/13 | **0** | Scorer extensions + grounding |
| 2026-07-09 | **50/50** | 37/37 | 13/13 | **0** | Deterministic extraction + baseline templates |
| 2026-07-10 | 47/50 live / 50/50 offline re-score | 34/37 live | 13/13 | **0** | Deny scorer v2 |

Reports: `eval/results/mrd-qa-smoke-*.{json,md}` and `eval/results/mrd-heldout-*.{json,md}`.

**The stable headline is the security row, not the quality row**: across dev and held-out runs — 0 forbidden-source leaks, 0 forbidden-claim leaks, and deny/isolation gates pass. Those checks are exact and were never relaxed.

## Methodology caveats

Honest interpretation of dev **50/50**:

1. **The scorer and the system were co-developed against the dev suite.** Held-out is reported separately.
2. **`SECURGRAPH_EVAL_BASELINE_TEMPLATES=1`** enables deterministic synthesis shortcuts for **dev baseline runs only** — never for held-out.
3. **The corpus is synthetic-realistic.** Small (17 docs); no human trust pilot.
4. **What is *not* caveated**: security metrics on held-out (primary generalization claim).

## C3 ablation (live)

`eval/c3_ablation_live.py`, report `c3-ablation-live-20260701T154455Z.md`:

| Run | Partitioning | Policy classes | Communities | Mixed communities | Probe leak |
| --- | --- | --- | --- | --- | --- |
| C3 on | yes | 14 | 89 | **0** | no |
| Ablation | no | 1 | 58 | **12** | no |
| C3 restored | yes | 14 | 89 | **0** | no |

## Reproducing

Full step-by-step from clean stack: [`docs/ARTIFACT_REPRODUCTION.md`](../ARTIFACT_REPRODUCTION.md).

```sh
make up && make migrate
make mrd-seed-live          # dev: API with SECURGRAPH_EVAL_BASELINE_TEMPLATES=1
make mrd-live

make heldout-seed-live      # held-out: API WITHOUT baseline templates
make heldout-live           # run twice; security metrics should match

make test-adversarial
make eval-repro             # validate corpus + optional live
```

## Open evaluation work

- Improve held-out allow quality (19/20) without touching frozen security cases.
- Human pilot for the trust/usability KPIs from the proposal.
- Latency/cost envelope per mode under the verifier ensemble.
