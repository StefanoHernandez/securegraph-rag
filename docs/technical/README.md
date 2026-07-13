# SecureGraph — Technical Documentation

Engineering documentation for people who want to **run, understand, extend, or audit** SecureGraph. Written against the code (each claim verified at the referenced commit), in reading order:

| # | Document | One-line summary |
| --- | --- | --- |
| 00 | [Overview](00-overview.md) | What SecureGraph is, the three guarantees (C1/C2/C3), glossary, document map. |
| 01 | [Architecture](01-architecture.md) | C4-style context/container/component views, the 8 layers, key design decisions. |
| 02 | [Data Model](02-data-model.md) | PostgreSQL schema + RLS, Qdrant collection, Neo4j graph model, OpenFGA model, migrations. |
| 03 | [API Reference](03-api-reference.md) | Every `/api/v1` endpoint, auth/CSRF mechanics, rate limits, error semantics. |
| 04 | [Query Pipeline](04-query-pipeline.md) | The LangGraph workflow node by node: routing, parallel lanes, consensus, synthesis verdict. |
| 05 | [Ingestion](05-ingestion.md) | Upload → parse → chunk → embed → graph extraction → FGA tuples; sync/async; self-healing. |
| 06 | [Security Model](06-security-model.md) | Threat model and every enforcement mechanism, with residual risks stated. |
| 07 | [Deployment](07-deployment.md) | Compose services/profiles, configuration surface, health probes, production checklist. |
| 08 | [Development](08-development.md) | Environment setup, test taxonomy, local verification, conventions, contribution flow. |
| 09 | [Evaluation](09-evaluation.md) | MRD corpus, scoring, current results with honest caveats, C3 ablation, reproduction. |

Suggested paths: *using it* → 00, 07, 03. *Working on it* → 00, 01, 04/05, 08. *Auditing it* → 06, 02, 09.

Related material outside this set: the [Operator Deployment Guide](../operator-deployment-guide.md) (step-by-step ops).

Maintained by Stefano Hernandez. When behavior changes, update the affected document in the same PR — stale documentation is treated as a bug.
