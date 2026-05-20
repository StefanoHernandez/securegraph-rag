"""Neo4j transaction writers.

Persists document metadata, chunks, and extracted knowledge graph slices in Neo4j.
"""

from __future__ import annotations

from typing import Any

from neo4j import AsyncDriver

from securgraph.domain import Chunk
from securgraph.domain.graph import ExtractedGraphSlice


async def write_graph_slice(
    driver: AsyncDriver,
    tenant_id: str,
    chunk: Chunk,
    graph_slice: ExtractedGraphSlice,
) -> None:
    """UPSERT Document -> Chunk -> Entities -> Relationships in Neo4j.

    Executes all three steps atomically within a single execute_write transaction.
    """
    async with driver.session() as session:
        await session.execute_write(
            _write_transaction,
            tenant_id=tenant_id,
            chunk=chunk,
            graph_slice=graph_slice,
        )


async def _write_transaction(
    tx: Any,
    tenant_id: str,
    chunk: Chunk,
    graph_slice: ExtractedGraphSlice,
) -> None:
    # 1. Upsert Chunk Node and Link to Document
    chunk_cypher = """
    MERGE (d:Document {document_id: $doc_id, tenant_id: $tenant_id})
    ON CREATE SET d.classification = $classification

    MERGE (c:Chunk {chunk_id: $chunk_id, tenant_id: $tenant_id})
    SET c.classification = $classification,
        c.policy_tags = $policy_tags

    MERGE (d)-[:CONTAINS]->(c)
    """
    await tx.run(
        chunk_cypher,
        doc_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        tenant_id=tenant_id,
        classification=chunk.classification.value,
        policy_tags=list(chunk.policy_tags),
    )

    # 2. Upsert Entities and Link to Chunk (only if entities exist)
    if graph_slice.entities:
        entity_cypher = """
        UNWIND $entities AS ent
        MERGE (e:Entity {name: ent.name, tenant_id: $tenant_id})
        ON CREATE SET e.label = ent.label

        WITH e
        MATCH (c:Chunk {chunk_id: $chunk_id, tenant_id: $tenant_id})
        MERGE (c)-[:MENTIONS]->(e)
        """
        await tx.run(
            entity_cypher,
            entities=[e.model_dump() for e in graph_slice.entities],
            tenant_id=tenant_id,
            chunk_id=chunk.chunk_id,
        )

    # 3. Upsert Relationships between Entities (only if relations exist)
    if graph_slice.relations:
        # Standard Cypher deduplication to avoid APOC dependency
        rel_cypher = """
        UNWIND $relations AS rel
        MATCH (src:Entity {name: rel.source_entity_name, tenant_id: $tenant_id})
        MATCH (tgt:Entity {name: rel.target_entity_name, tenant_id: $tenant_id})

        MERGE (src)-[r:RELATED_TO {type: rel.relationship_type}]->(tgt)
        SET r.confidence = rel.confidence,
            r.source_chunk_ids = case
                when $chunk_id in coalesce(r.source_chunk_ids, [])
                then r.source_chunk_ids
                else coalesce(r.source_chunk_ids, []) + [$chunk_id]
            end
        """
        await tx.run(
            rel_cypher,
            relations=[r.model_dump() for r in graph_slice.relations],
            tenant_id=tenant_id,
            chunk_id=chunk.chunk_id,
        )
