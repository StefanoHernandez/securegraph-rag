"""Hierarchical community partitioner and writer for Neo4j Knowledge Graphs."""

from __future__ import annotations

import json
from typing import Any

import networkx as nx
from neo4j import AsyncDriver
from networkx.algorithms.community import louvain_communities

from securgraph.adapters.ollama import get_ollama_client
from securgraph.community.policy_class import AccessLabel, partition_edges_by_class
from securgraph.config import get_settings
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


async def generate_hierarchical_communities(
    driver: AsyncDriver,
    tenant_id: str,
    fga: Any | None = None,
) -> None:
    """Partition the tenant graph into policy-equivalence-classes, run Louvain
    per class, summarize, and write class-tagged Community nodes. If ``fga`` is
    provided, emit OpenFGA authorization tuples (Task 6 wires this)."""
    _LOG.info("generating_communities_started", tenant_id=tenant_id)

    # 1. Fetch chunk access labels
    async with driver.session() as session:
        chunk_rows_result = await session.run(
            "MATCH (c:Chunk {tenant_id: $tenant_id}) "
            "RETURN c.chunk_id AS chunk_id, c.owner_id AS owner_id, "
            "c.policy_tags AS policy_tags, c.direct_viewers AS direct_viewers",
            tenant_id=tenant_id,
        )
        chunk_rows = await chunk_rows_result.data()

        edges_result = await session.run(
            "MATCH (src:Entity {tenant_id: $tenant_id})-[r:RELATED_TO]->"
            "(tgt:Entity {tenant_id: $tenant_id}) "
            "RETURN src.name AS source, tgt.name AS target, r.type AS type, "
            "r.confidence AS confidence, r.source_chunk_ids AS source_chunk_ids",
            tenant_id=tenant_id,
        )
        edges = await edges_result.data()

    chunk_labels = {
        row["chunk_id"]: AccessLabel(
            owner=row.get("owner_id") or "",
            group_viewers=frozenset(row.get("policy_tags") or []),
            direct_viewers=frozenset(row.get("direct_viewers") or []),
        )
        for row in chunk_rows
    }

    if not edges:
        _LOG.info("generating_communities_no_edges", tenant_id=tenant_id)
        return

    buckets = partition_edges_by_class(edges, chunk_labels)

    # 2. Clear existing communities for the tenant
    async with driver.session() as session:
        await session.run(
            "MATCH (c:Community {tenant_id: $tenant_id}) DETACH DELETE c",
            tenant_id=tenant_id,
        )

    settings = get_settings()
    client = get_ollama_client()
    written: list[dict[str, Any]] = []

    # 3. Per class: Louvain level0 + level1, summarize, write
    for class_key, class_edges in buckets.items():
        g = nx.Graph()
        for ce in class_edges:
            g.add_edge(ce.source, ce.target, weight=ce.confidence)
        if g.number_of_edges() == 0:
            continue
        try:
            level_0 = list(louvain_communities(g, resolution=1.2, seed=42))
            level_1 = list(louvain_communities(g, resolution=0.6, seed=42))
        except Exception as e:
            _LOG.error("louvain_partition_failed", error=str(e), class_key=class_key)
            level_0 = [set(g.nodes())]
            level_1 = [set(g.nodes())]

        for level, comms in ((0, level_0), (1, level_1)):
            for idx, members in enumerate(comms):
                comm = await _summarize_and_write_community(
                    driver, client, settings, tenant_id,
                    class_key, class_edges, members, level, idx,
                )
                written.append(comm)

    # 4. Parent/child HAS_SUB_COMMUNITY: only within the same class
    async with driver.session() as session:
        for parent in written:
            if parent["level"] != 1:
                continue
            for child in written:
                if child["level"] != 0:
                    continue
                if parent["policy_class_key"] != child["policy_class_key"]:
                    continue
                if parent["members"] & child["members"]:
                    await session.run(
                        "MATCH (p:Community {community_id: $parent_id, tenant_id: $tenant_id}) "
                        "MATCH (c:Community {community_id: $child_id, tenant_id: $tenant_id}) "
                        "MERGE (p)-[:HAS_SUB_COMMUNITY]->(c)",
                        parent_id=parent["community_id"],
                        child_id=child["community_id"],
                        tenant_id=tenant_id,
                    )

    _LOG.info(
        "generating_communities_completed",
        tenant_id=tenant_id, classes=len(buckets), communities=len(written),
    )

    if fga is not None:
        await emit_community_authorization_tuples(fga, chunk_labels, buckets, written)


async def _summarize_and_write_community(
    driver: AsyncDriver,
    client: Any,
    settings: Any,
    tenant_id: str,
    class_key: str,
    class_edges: list[Any],
    members: set[str],
    level: int,
    idx: int,
) -> dict[str, Any]:
    member_list = list(members)

    comm_edges = [
        f"({ce.source}) -[{ce.type}]-> ({ce.target})"
        for ce in class_edges
        if ce.source in members and ce.target in members
    ]

    system_prompt = (
        "You are an expert knowledge graph summarization assistant.\n"
        "Generate a highly descriptive title and a structured, detailed natural "
        "language summary for this cluster of entities and relationships.\n"
        "Output strictly in JSON matching this schema:\n"
        '{\n  "title": "A short descriptive title",\n'
        '  "summary": "A detailed paragraph synthesizing what these entities '
        'represent and how they relate."\n}\n'
        "Return ONLY raw JSON, no markdown."
    )
    prompt = (
        f"Level: {level}\n"
        f"Entities in Cluster: {', '.join(member_list)}\n"
        f"Relationships in Cluster:\n" + "\n".join(comm_edges[:15])
    )

    try:
        response = await client.chat(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            format="json",
            options={"num_ctx": settings.llm_num_ctx},
        )
        content = (response.message.content or "{}").strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        data = json.loads(content)
        title = data.get("title") or f"Community L{level} {idx}"
        summary = data.get("summary") or "No summary generated."
    except Exception as e:
        _LOG.warning("community_summarization_failed", idx=idx, error=str(e))
        title = f"Community L{level} {idx}"
        summary = f"Group of entities: {', '.join(member_list)}."

    comm_id = f"comm_l{level}_{idx}_{class_key}_{tenant_id}"
    async with driver.session() as session:
        await session.run(
            "MERGE (c:Community {community_id: $comm_id, tenant_id: $tenant_id}) "
            "SET c.title = $title, c.summary = $summary, c.level = $level, "
            "    c.member_entities = $member_entities, "
            "    c.policy_class_key = $policy_class_key",
            comm_id=comm_id, tenant_id=tenant_id, title=title, summary=summary,
            level=level, member_entities=member_list, policy_class_key=class_key,
        )
        await session.run(
            "UNWIND $member_entities AS ent_name "
            "MATCH (c:Community {community_id: $comm_id, tenant_id: $tenant_id}) "
            "MATCH (e:Entity {name: ent_name, tenant_id: $tenant_id}) "
            "MERGE (c)-[:CONTAINS_ENTITY]->(e)",
            comm_id=comm_id, tenant_id=tenant_id, member_entities=member_list,
        )

    return {
        "community_id": comm_id,
        "policy_class_key": class_key,
        "title": title,
        "summary": summary,
        "members": members,
        "level": level,
    }


async def emit_community_authorization_tuples(
    fga: Any,
    chunk_labels: dict[str, AccessLabel],
    buckets: dict[str, list[Any]],
    written: list[dict[str, Any]],
) -> None:
    """Stub — replaced in Task 6."""
    return None
