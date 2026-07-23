"""Hierarchical community partitioner and writer for Neo4j Knowledge Graphs."""

from __future__ import annotations

import os
from typing import Any

import networkx as nx
from neo4j import AsyncDriver
from networkx.algorithms.community import louvain_communities
from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

from securgraph.adapters.llm import get_llm_adapter, robust_json_loads
from securgraph.adapters.llm.base import LLMAdapter
from securgraph.community.policy_class import (
    UNLABELED_CLASS_KEY,
    AccessLabel,
    ClassEdge,
    canonical_group_userset,
    canonical_user_principal,
    partition_edges_by_class,
    policy_class_key,
)
from securgraph.logging import get_logger
from securgraph.runtime_overrides import get_effective_settings

_LOG = get_logger(__name__)


def _edge_policy_class_keys(
    edge: dict[str, Any],
    chunk_labels: dict[str, AccessLabel],
) -> frozenset[str]:
    keys: set[str] = set()
    for chunk_id in edge.get("source_chunk_ids") or []:
        label = chunk_labels.get(chunk_id)
        keys.add(policy_class_key(label) if label is not None else UNLABELED_CLASS_KEY)
    if not keys:
        keys.add(UNLABELED_CLASS_KEY)
    return frozenset(keys)


def _edges_to_class_edges(
    edges: list[dict[str, Any]],
    chunk_labels: dict[str, AccessLabel],
) -> list[ClassEdge]:
    """Convert raw edge dicts to ClassEdge without policy partitioning (ablation)."""
    return [
        ClassEdge(
            source=edge["source"],
            target=edge["target"],
            type=edge["type"],
            confidence=float(edge.get("confidence", 0.5)),
            source_policy_class_keys=_edge_policy_class_keys(edge, chunk_labels),
        )
        for edge in edges
    ]


async def generate_hierarchical_communities(
    driver: AsyncDriver,
    tenant_id: str,
    fga: Any | None = None,
) -> dict[str, int]:
    """Partition the tenant graph into policy-equivalence-classes, run Louvain
    per class, summarize, and write class-tagged Community nodes. If ``fga`` is
    provided, emit OpenFGA authorization tuples (Task 6 wires this).

    C3 ablation: set ``SECURGRAPH_C3_ABLATION_DISABLE_PARTITION=true`` to skip
    policy-equivalence-class partitioning and run Louvain on the whole tenant
    graph as a single bucket. Used only for the C3 paper ablation study.
    """
    _LOG.info("generating_communities_started", tenant_id=tenant_id)

    c3_ablation = os.environ.get("SECURGRAPH_C3_ABLATION_DISABLE_PARTITION", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if c3_ablation:
        _LOG.warning("c3_ablation_disable_partition_active", tenant_id=tenant_id)

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
        return {"classes": 0, "communities": 0}

    if c3_ablation:
        # Ablation: skip policy partitioning, treat all edges as one bucket.
        buckets = {UNLABELED_CLASS_KEY: _edges_to_class_edges(edges, chunk_labels)}
    else:
        buckets = partition_edges_by_class(edges, chunk_labels)

    # 2. Clear existing communities for the tenant
    async with driver.session() as session:
        await session.run(
            "MATCH (c:Community {tenant_id: $tenant_id}) DETACH DELETE c",
            tenant_id=tenant_id,
        )

    settings = get_effective_settings(tenant_id)
    adapter = get_llm_adapter(tenant_id=tenant_id)
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
                    driver,
                    adapter,
                    settings,
                    tenant_id,
                    class_key,
                    class_edges,
                    members,
                    level,
                    idx,
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
        tenant_id=tenant_id,
        classes=len(buckets),
        communities=len(written),
    )

    if fga is not None:
        await emit_community_authorization_tuples(fga, chunk_labels, buckets, written)
    return {"classes": len(buckets), "communities": len(written)}


async def _summarize_and_write_community(
    driver: AsyncDriver,
    adapter: LLMAdapter,
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
    source_policy_class_keys = sorted(
        {
            key
            for ce in class_edges
            if ce.source in members and ce.target in members
            for key in (ce.source_policy_class_keys or frozenset({class_key}))
        }
        or {class_key}
    )

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
        content = await adapter.chat(
            system=system_prompt,
            user=prompt,
            json_mode=True,
            think=settings.llm_thinking,
            keep_alive=300,
        )
        data = robust_json_loads(content)
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
            "    c.policy_class_key = $policy_class_key, "
            "    c.source_policy_class_keys = $source_policy_class_keys",
            comm_id=comm_id,
            tenant_id=tenant_id,
            title=title,
            summary=summary,
            level=level,
            member_entities=member_list,
            policy_class_key=class_key,
            source_policy_class_keys=source_policy_class_keys,
        )
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
        "policy_class_key": class_key,
        "title": title,
        "summary": summary,
        "members": members,
        "level": level,
        "source_policy_class_keys": source_policy_class_keys,
    }


def _fga_write_client(fga: Any) -> Any:
    """Accept either OpenFGAPolicyAdapter or a raw OpenFGA client."""
    return getattr(fga, "client", None) or fga


def _is_duplicate_tuple_error(exc: Exception) -> bool:
    return "already exists" in str(exc).lower()


async def _write_fga_tuple_batch(writer: Any, tuples: list[ClientTuple]) -> None:
    try:
        await writer.write(ClientWriteRequest(writes=tuples))
        return
    except Exception as exc:
        if not _is_duplicate_tuple_error(exc):
            raise

    for item in tuples:
        try:
            await writer.write(ClientWriteRequest(writes=[item]))
        except Exception as exc:
            if _is_duplicate_tuple_error(exc):
                _LOG.debug(
                    "openfga_duplicate_tuple_skipped",
                    user=item.user,
                    relation=item.relation,
                    object=item.object,
                )
                continue
            raise


async def emit_community_authorization_tuples(
    fga: Any,
    chunk_labels: dict[str, AccessLabel],
    buckets: dict[str, list[Any]],
    written: list[dict[str, Any]],
) -> None:
    """Emit policy_class grant tuples and community in_class tuples.

    One ``policy_class`` per distinct class key with grants mirroring the
    access label (owner, group_viewer per group, direct_viewer per direct user).
    One ``community in_class policy_class`` tuple per written community.
    A class with no grants (e.g. ``UNLABELED_CLASS_KEY`` or absent label) emits
    no grant tuples and therefore remains invisible at retrieval (fail-closed).
    """
    label_by_key: dict[str, AccessLabel] = {}
    for label in chunk_labels.values():
        label_by_key.setdefault(policy_class_key(label), label)

    tuples: list[ClientTuple] = []
    for key in buckets:
        lbl = label_by_key.get(key)
        if lbl is None:
            continue
        obj = f"policy_class:{key}"
        if lbl.owner:
            tuples.append(
                ClientTuple(
                    user=canonical_user_principal(lbl.owner),
                    relation="owner",
                    object=obj,
                )
            )
        canonical_groups = sorted(
            {canonical_group_userset(group) for group in lbl.group_viewers if group}
        )
        for group in canonical_groups:
            tuples.append(
                ClientTuple(
                    user=group,
                    relation="group_viewer",
                    object=obj,
                )
            )
        canonical_viewers = sorted(
            {canonical_user_principal(viewer) for viewer in lbl.direct_viewers if viewer}
        )
        for viewer in canonical_viewers:
            tuples.append(
                ClientTuple(
                    user=viewer,
                    relation="direct_viewer",
                    object=obj,
                )
            )

    for comm in written:
        tuples.append(
            ClientTuple(
                user=f"policy_class:{comm['policy_class_key']}",
                relation="in_class",
                object=f"community:{comm['community_id']}",
            )
        )

    if tuples:
        # OpenFGA limits writes to 100 tuples per request; chunk to stay under.
        batch_size = 80
        writer = _fga_write_client(fga)
        for i in range(0, len(tuples), batch_size):
            await _write_fga_tuple_batch(writer, tuples[i : i + batch_size])
