"""Hierarchical community partitioner and writer for Neo4j Knowledge Graphs."""

from __future__ import annotations

import json
from typing import Any

from neo4j import AsyncDriver
import networkx as nx
from networkx.algorithms.community import louvain_communities

from securgraph.adapters.ollama import get_ollama_client
from securgraph.config import get_settings
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


async def generate_hierarchical_communities(
    driver: AsyncDriver,
    tenant_id: str,
) -> None:
    """Read the tenant's subgraph from Neo4j, compute Louvain partitions, generate summaries, and save communities."""
    _LOG.info("generating_communities_started", tenant_id=tenant_id)
    
    # 1. Fetch all entities and relationships for the tenant
    async with driver.session() as session:
        nodes_result = await session.run(
            "MATCH (e:Entity {tenant_id: $tenant_id}) RETURN e.name AS name",
            tenant_id=tenant_id,
        )
        nodes = [record["name"] for record in await nodes_result.data()]

        edges_result = await session.run(
            "MATCH (src:Entity {tenant_id: $tenant_id})-[r:RELATED_TO]->(tgt:Entity {tenant_id: $tenant_id}) "
            "RETURN src.name AS source, tgt.name AS target, r.type AS type, r.confidence AS confidence",
            tenant_id=tenant_id,
        )
        edges = await edges_result.data()

    if not nodes:
        _LOG.info("generating_communities_empty_graph", tenant_id=tenant_id)
        return

    # 2. Build the NetworkX Graph
    g = nx.Graph()
    g.add_nodes_from(nodes)
    for edge in edges:
        g.add_edge(edge["source"], edge["target"], weight=edge.get("confidence", 0.5))

    if g.number_of_edges() == 0:
        _LOG.info("generating_communities_no_edges", tenant_id=tenant_id)
        # Even with no edges, we can group all entities into a single generic community
        default_comm = set(nodes)
        level_0_comms = [default_comm]
        level_1_comms = [default_comm]
    else:
        # Run Louvain with different resolution parameters for hierarchical clustering
        try:
            # Level 0: Fine-grained communities
            level_0_comms = list(louvain_communities(g, resolution=1.2, seed=42))
            # Level 1: Coarse-grained high-level communities
            level_1_comms = list(louvain_communities(g, resolution=0.6, seed=42))
        except Exception as e:
            _LOG.error("louvain_partition_failed", error=str(e))
            level_0_comms = [set(nodes)]
            level_1_comms = [set(nodes)]

    # 3. Clear existing Community nodes and relations for this tenant
    async with driver.session() as session:
        await session.run(
            "MATCH (c:Community {tenant_id: $tenant_id}) DETACH DELETE c",
            tenant_id=tenant_id,
        )

    # 4. Generate Summaries and Write to Neo4j
    # We will compute summaries for both Level 0 and Level 1
    settings = get_settings()
    client = get_ollama_client()

    async def summarize_community(members: set[str], level: int, idx: int) -> dict[str, Any]:
        member_list = list(members)
        
        # Filter edges associated with these members
        comm_edges = []
        for edge in edges:
            if edge["source"] in members and edge["target"] in members:
                comm_edges.append(f"({edge['source']}) -[{edge['type']}]-> ({edge['target']})")
        
        system_prompt = (
            "You are an expert knowledge graph summarization assistant.\n"
            "Generate a highly descriptive title and a structured, detailed natural language summary for this cluster of entities and relationships.\n"
            "Output strictly in JSON matching this schema:\n"
            "{\n"
            '  "title": "A short descriptive title (e.g. SSL Encryption Protocols)",\n'
            '  "summary": "A detailed natural language paragraph synthesizing what these entities represent and how they relate."\n'
            "}\n"
            "Do not write markdown blocks or text, return ONLY the raw JSON."
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
                    {"role": "user", "content": prompt}
                ],
                format="json",
                options={"num_ctx": settings.llm_num_ctx}
            )
            content = response.message.content or "{}"
            
            # Parse response JSON
            content_stripped = content.strip()
            if content_stripped.startswith("```"):
                lines = content_stripped.splitlines()
                if lines[0].startswith("```json") or lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content_stripped = "\n".join(lines).strip()

            data = json.loads(content_stripped)
            title = data.get("title") or f"Community Level {level} Cluster {idx}"
            summary = data.get("summary") or "No summary generated."
        except Exception as e:
            _LOG.warning("community_summarization_failed", idx=idx, error=str(e))
            title = f"Community Level {level} Cluster {idx}"
            summary = f"Group of entities: {', '.join(member_list)}."

        comm_id = f"comm_l{level}_{idx}_{tenant_id}"
        
        # Write to Neo4j
        async with driver.session() as session:
            # Create Community node
            await session.run(
                "MERGE (c:Community {community_id: $comm_id, tenant_id: $tenant_id}) "
                "SET c.title = $title, "
                "    c.summary = $summary, "
                "    c.level = $level, "
                "    c.member_entities = $member_entities",
                comm_id=comm_id,
                tenant_id=tenant_id,
                title=title,
                summary=summary,
                level=level,
                member_entities=member_list,
            )

            # Link Community to member Entities
            await session.run(
                "UNWIND $member_entities AS ent_name "
                "MATCH (c:Community {community_id: $comm_id, tenant_id: $tenant_id}) "
                "MATCH (e:Entity {name: ent_name, tenant_id: $tenant_id}) "
                "MERGE (c)-[:CONTAINS_ENTITY]->(e)",
                comm_id=comm_id,
                tenant_id=tenant_id,
                member_entities=member_list,
            )

        return {
            "community_id": comm_id,
            "title": title,
            "summary": summary,
            "members": members,
            "level": level,
        }

    # Generate Level 0 (fine-grained) summaries
    level_0_results = []
    for i, comm in enumerate(level_0_comms):
        res = await summarize_community(comm, level=0, idx=i)
        level_0_results.append(res)

    # Generate Level 1 (thematic) summaries
    level_1_results = []
    for i, comm in enumerate(level_1_comms):
        res = await summarize_community(comm, level=1, idx=i)
        level_1_results.append(res)

    # 5. Connect parent-child hierarchies
    # If a Level 1 community has non-empty member intersection with a Level 0 community,
    # we link Level 1 as the parent of Level 0
    async with driver.session() as session:
        for parent in level_1_results:
            for child in level_0_results:
                # If they share members, link them in Neo4j hierarchy
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
        tenant_id=tenant_id,
        level_0_count=len(level_0_comms),
        level_1_count=len(level_1_comms),
    )
