# 05 — Ingestion

`POST /api/v1/ingest` turns an uploaded file into: a document + chunks in PostgreSQL, dense vectors in Qdrant, an entity subgraph in Neo4j, and authorization tuples in OpenFGA — with provenance recorded at every step.

## Sync vs async

```mermaid
flowchart TB
    UP["POST /ingest (multipart)"] --> HARD["Upload hardening:<br/>extension allowlist · 50 MB cap ·<br/>filename sanitize · owner checks"]
    HARD --> FLAG{SECURGRAPH_FEATURE_WORKERS_ENABLED}
    FLAG -->|false (default)| SYNC["Run pipeline in-request<br/>→ 201 with document_id + chunks_created"]
    FLAG -->|true| ASYNC["Persist job (queued) +<br/>AES-GCM-encrypted source →<br/>enqueue arq task → 202 with job_id"]
    ASYNC -->|Redis down| E503["503 — explicit error,<br/>never silent"]
    SYNC --> PIPE
    ASYNC --> W["arq worker:<br/>RLS context → job running → pipeline"] --> PIPE["Ingestion pipeline"]
```

- **Sync (default)**: no Redis required at all — the default compose profile runs no Redis container and uploads work.
- **Async (`workers` profile)**: the original upload is retained AES-GCM-encrypted (`ingestion_sources`, AAD-bound to tenant+job) so failed jobs can be retried from `/tenants/{t}/ingest-jobs/{job}/retry`; a reconcile endpoint marks jobs stuck `running` > 10 min as failed.

Upload hardening: extension allowlist `.md .markdown .txt .pdf .docx .xlsx`, 50 MB cap, path/control-character stripping on the filename, empty-file rejection, `owner_id` namespace validation, and ownership delegation only for tenant admins to active members. Ingested document content is treated as untrusted input downstream (prompt-injection trust boundary).

## Pipeline stages

| # | Stage | What happens | Key facts |
| --- | --- | --- | --- |
| 1 | Idempotency | `source_hash` over the bytes; existing `(tenant_id, source_uri, source_hash)` → `already_ingested`, zero writes. | |
| 2 | Parse | Format detection + parser (markdown/text/PDF/DOCX/XLSX) → sections. Empty output fails the job. | |
| 3 | Chunk | Section-aware greedy paragraph packing under a real tokenizer (HF `ibm-granite/granite-4.0-h-tiny`). | target 500–900 tokens, hard max 1200, overlap 100; stable ids `{doc}_{ordinal}_{hash8}`; chunks doubly linked (`previous/next_chunk_id`). |
| 4 | Persist | `documents` + `chunks` rows; classification and `policy_tags` copied onto every chunk. | |
| 5 | Embed + index | BGE-M3 dense vectors (local FlagEmbedding, 1024-dim — no network egress) upserted to Qdrant with tenant/classification/tags payload. | Point id `uuid5(chunk_id)` → idempotent. |
| 6 | Authorize | OpenFGA tuples: `owner` + `direct_viewer` on the document, `parent_document` per chunk (chunks inherit document access). Batch writer tolerates duplicates but **fails closed on real store errors** (SGH-06: any store failure aborts before later stores write). | |
| 7 | Graph extract | Per chunk, the `extract`-stage LLM produces entities + typed relations (JSON mode) → Neo4j `MERGE` (`Entity`, `MENTIONS`, `RELATED_TO` with `source_chunk_ids`). Chunk nodes carry `owner_id`, `direct_viewers`, `policy_tags` — the raw material for C3. | |
| 8 | Provenance | `ingestion_provenance` row with parser/chunker/embedder/extractor versions, chunk count, errors; job → `succeeded`. | |

Community detection is **not** part of ingestion — it runs as an explicit tenant-admin job (`community-recompute`, see [06 — Security Model](06-security-model.md#c3--policy-partitioned-communities)) because it is tenant-global.

## Self-healing

When `SECURGRAPH_FEATURE_SELF_HEALING_ENABLED=true` and the consensus verifier finds a graph claim contradicted by the vector channel, a background repair task runs:

1. The `judge` LLM examines the contradiction and returns `{action: delete | update | flag, corrected_target_entity, reason}`.
2. `delete` removes the edge; `flag` marks it (`confidence=0.1`, `needs_review`, `conflict_reason`).
3. `update` replaces the edge — and **inherits the replaced edge's `source_chunk_ids`** onto the corrected edge (`self_healed=true`). This matters: path-cut pruning authorizes paths via chunk ids, so a healed edge without real chunk ids would be permanently unauthorized. Fallback when the old edge had none: empty list (unconstrained, same as any sourceless edge).

## Operational notes

- Job lifecycle is queryable: `GET /ingest/status/{job_id}` (requester/tenant-scoped) and the tenant admin job lists under `/tenants/{t}/ingest-jobs`.
- Chaos coverage: `make test-sgh06` (unit failure-injection across Postgres/Qdrant/OpenFGA/Neo4j) and `make test-ingest-chaos-live` (live reconcile/worker chaos).
- First run needs the HF tokenizer cached (network); afterwards chunking is offline. Vendor the tokenizer if the environment is fully air-gapped.
