# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| `feat/submission-ready` / `0.1.0a0` | Active development |

## Reporting a vulnerability

Do **not** open a public GitHub issue for security-sensitive reports.

Open a private GitHub security advisory at
`https://github.com/StefanoHernandez/securegraph-rag/security/advisories/new`
and include:

- Affected component (API, web UI, ingestion worker, evaluation harness)
- Reproduction steps or proof-of-concept
- Impact assessment (confidentiality, integrity, availability)

We aim to acknowledge reports within 5 business days.

## Scope

In scope: authorization bypass, cross-tenant data exposure, audit-chain tampering,
anchor bypass, and ingestion cross-store residue.

Out of scope: denial-of-service against local dev stacks without a deployed
production configuration, and issues requiring physical access to operator hardware.
