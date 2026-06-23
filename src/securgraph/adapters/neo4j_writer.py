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
    *,
    owner_id: str = "",
    direct_viewers: tuple[str, ...] = (),
) -> None:
    """UPSERT Document -> Chunk -> Entities -> Relationships in Neo4j.

    `owner_id` and `direct_viewers` are persisted on the Chunk node so the
    community generator can compute a policy-equivalence-class key from each
    chunk's full authorization label (owner, groups, direct viewers).
    """
    async with driver.session() as session:
        await session.execute_write(
            _write_transaction,
            tenant_id=tenant_id,
            chunk=chunk,
            graph_slice=graph_slice,
            owner_id=owner_id,
            direct_viewers=direct_viewers,
        )


async def _write_transaction(
    tx: Any,
    tenant_id: str,
    chunk: Chunk,
    graph_slice: ExtractedGraphSlice,
    owner_id: str = "",
    direct_viewers: tuple[str, ...] = (),
) -> None:
    # 1. Upsert Chunk Node and Link to Document
    chunk_cypher = """
    MERGE (d:Document {document_id: $doc_id, tenant_id: $tenant_id})
    ON CREATE SET d.classification = $classification

    MERGE (c:Chunk {chunk_id: $chunk_id, tenant_id: $tenant_id})
    SET c.classification = $classification,
        c.policy_tags = $policy_tags,
        c.owner_id = $owner_id,
        c.direct_viewers = $direct_viewers

    MERGE (d)-[:CONTAINS]->(c)
    """
    await tx.run(
        chunk_cypher,
        doc_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        tenant_id=tenant_id,
        classification=chunk.classification.value,
        policy_tags=list(chunk.policy_tags),
        owner_id=owner_id,
        direct_viewers=list(direct_viewers),
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


async def flag_or_delete_relationship(
    driver: AsyncDriver,
    tenant_id: str,
    source_name: str,
    target_name: str,
    action: str,
    new_target_name: str | None = None,
    reason: str | None = None,
) -> None:
    """Flag, delete, or update an incorrect relationship in Neo4j."""
    async with driver.session() as session:
        await session.execute_write(
            _repair_transaction,
            tenant_id=tenant_id,
            source_name=source_name,
            target_name=target_name,
            action=action,
            new_target_name=new_target_name,
            reason=reason,
        )


async def _repair_transaction(
    tx: Any,
    tenant_id: str,
    source_name: str,
    target_name: str,
    action: str,
    new_target_name: str | None = None,
    reason: str | None = None,
) -> None:
    if action == "delete":
        cypher = """
        MATCH (src:Entity {name: $source_name, tenant_id: $tenant_id})-[r:RELATED_TO]->(tgt:Entity {name: $target_name, tenant_id: $tenant_id})
        DELETE r
        """
        await tx.run(cypher, source_name=source_name, target_name=target_name, tenant_id=tenant_id)
    elif action == "flag":
        cypher = """
        MATCH (src:Entity {name: $source_name, tenant_id: $tenant_id})-[r:RELATED_TO]->(tgt:Entity {name: $target_name, tenant_id: $tenant_id})
        SET r.confidence = 0.1,
            r.needs_review = true,
            r.conflict_reason = $reason
        """
        await tx.run(
            cypher,
            source_name=source_name,
            target_name=target_name,
            tenant_id=tenant_id,
            reason=reason or "Flagged due to RAG consensus contradiction."
        )
    elif action == "update":
        delete_cypher = """
        MATCH (src:Entity {name: $source_name, tenant_id: $tenant_id})-[r:RELATED_TO]->(tgt:Entity {name: $target_name, tenant_id: $tenant_id})
        DELETE r
        """
        await tx.run(delete_cypher, source_name=source_name, target_name=target_name, tenant_id=tenant_id)

        if new_target_name:
            update_cypher = """
            MATCH (src:Entity {name: $source_name, tenant_id: $tenant_id})
            MERGE (tgt:Entity {name: $new_target_name, tenant_id: $tenant_id})
            ON CREATE SET tgt.label = "Entity"
            MERGE (src)-[r:RELATED_TO]->(tgt)
            SET r.confidence = 0.9,
                r.needs_review = false,
                r.source_chunk_ids = ["self_healing"]
            """
            await tx.run(update_cypher, source_name=source_name, new_target_name=new_target_name, tenant_id=tenant_id)
