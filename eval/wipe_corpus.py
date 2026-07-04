"""Tenant-scoped wipe for Postgres + Qdrant + Neo4j.

Run before a baseline harness invocation to remove residual data from prior
runs. The baseline measures retrieval quality against a known corpus; stale
chunks from previous ingests would let FGA fail-closed surface phantom
"Access denied" warnings and would dilute graph mode with off-topic entities.

Scope: a single tenant_id (default "mrd"). The wipe is per-store:

  - Postgres: DELETE FROM <table> WHERE tenant_id = $1, for every table that
    carries a tenant_id column (documents, chunks, policy_decisions,
    ingestion_jobs, ingestion_provenance, query_traces, retrieval_evidence).
  - Qdrant: delete points whose payload.tenant_id matches.
  - Neo4j: DETACH DELETE every node carrying tenant_id = $1 (Entity, Chunk,
    Community).

OpenFGA tuples are NOT wiped — they are idempotent on re-seed and removing
them adds no observable signal to the baseline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, FilterSelector, MatchValue
from sqlalchemy import text

from securgraph.adapters.neo4j import get_neo4j_driver
from securgraph.api.dependencies import AsyncSessionLocal
from securgraph.config import get_settings

PG_TABLES_WITH_TENANT = (
    "retrieval_evidence",
    "query_traces",
    "ingestion_provenance",
    "ingestion_jobs",
    "policy_decisions",
    "chunks",
    "documents",
)


async def wipe_postgres(tenant_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    async with AsyncSessionLocal() as session:
        for table in PG_TABLES_WITH_TENANT:
            result = await session.execute(
                # {table} comes from the trusted PG_TABLES_WITH_TENANT constant,
                # never user input; the tenant value is bound as a parameter.
                text(f"DELETE FROM {table} WHERE tenant_id = :tid"),  # noqa: S608
                {"tid": tenant_id},
            )
            counts[table] = result.rowcount or 0
        await session.commit()
    return counts


def wipe_qdrant(tenant_id: str) -> int:
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    flt = Filter(must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))])
    try:
        before = client.count(settings.qdrant_collection, count_filter=flt).count
    except Exception:
        before = -1
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=FilterSelector(filter=flt),
    )
    return before


async def wipe_neo4j(tenant_id: str) -> int:
    driver = get_neo4j_driver()
    async with driver.session() as session:
        result = await session.run(
            "MATCH (n) WHERE n.tenant_id = $tid DETACH DELETE n RETURN count(n) AS deleted",
            tid=tenant_id,
        )
        record = await result.single()
        return int(record["deleted"]) if record else 0


async def main_async(tenant_id: str) -> None:
    print(f"Wiping tenant_id={tenant_id!r}")
    print("--- Postgres ---")
    pg_counts = await wipe_postgres(tenant_id)
    for t, n in pg_counts.items():
        print(f"  {t}: {n} rows")
    print("--- Qdrant ---")
    q_count = wipe_qdrant(tenant_id)
    print(f"  points removed (pre-count): {q_count}")
    print("--- Neo4j ---")
    n4 = await wipe_neo4j(tenant_id)
    print(f"  nodes deleted: {n4}")
    print("Done.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="mrd", help="Tenant id to wipe (default: mrd)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt",
    )
    args = parser.parse_args()
    if not args.yes:
        confirm = input(
            f"This will DELETE all rows/points/nodes with tenant_id={args.tenant!r}. "
            "Type the tenant to confirm: "
        )
        if confirm.strip() != args.tenant:
            print("Aborted.")
            sys.exit(1)
    asyncio.run(main_async(args.tenant))


if __name__ == "__main__":
    main()
