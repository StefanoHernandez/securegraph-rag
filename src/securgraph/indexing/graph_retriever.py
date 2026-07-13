"""Neo4j graph traversal retriever.

Traverses entity relationships to retrieve semantic graph paths.
"""

from __future__ import annotations

import hashlib

from neo4j import AsyncDriver
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.domain import Chunk, OpenFGAPolicyAdapter, QueryScope, QueryScopeError, Subject
from securgraph.domain.base import utcnow
from securgraph.domain.graph import GraphPath
from securgraph.domain.policy import (
    PolicyDecision,
    PolicyDecisionValue,
    PolicyObjectType,
)
from securgraph.indexing.postgres_writer import fetch_chunks_by_ids, write_policy_decisions


class Neo4jGraphRetriever:
    """Traverses knowledge graph paths in Neo4j up to N hops deep."""

    def __init__(self, driver: AsyncDriver) -> None:
        self.driver = driver

    async def traverse_subgraph(
        self, tenant_id: str, seed_entities: list[str], max_depth: int = 2
    ) -> list[GraphPath]:
        """Fetch all entities and relationships traversed from the seed nodes.

        Limits maximum path depth strictly between 1 and 4 for protection.
        Generates stable deterministic path IDs using MD5 hashes.
        """
        if not seed_entities:
            return []

        # Restrict depth to safe bounds to prevent resource exhaustion
        depth = max(1, min(max_depth, 4))

        # Dynamically build path match length safely (since depth is a validated int)
        cypher = f"""
        MATCH (seed:Entity {{tenant_id: $tenant_id}})
        WHERE seed.name IN $seeds

        MATCH path = (seed)-[:RELATED_TO*1..{depth}]-(other:Entity {{tenant_id: $tenant_id}})

        RETURN
            [n in nodes(path) | {{id: n.name, label: labels(n)[0], name: n.name}}] AS path_nodes,
            [rel in relationships(path) | {{
                id: elementId(rel),
                source: startNode(rel).name,
                target: endNode(rel).name,
                type: rel.type,
                source_chunk_ids: rel.source_chunk_ids
            }}] AS path_edges
        """

        async with self.driver.session() as session:
            result = await session.run(cypher, tenant_id=tenant_id, seeds=seed_entities)
            paths = []
            seen_path_ids = set()

            async for record in result:
                path_nodes = record["path_nodes"]
                path_edges = record["path_edges"]

                nodes = tuple(n["id"] for n in path_nodes)
                edges = tuple(e["id"] for e in path_edges)

                # Aggregating source chunk IDs across the path
                source_chunks = set()
                for edge in path_edges:
                    chunk_ids = edge.get("source_chunk_ids")
                    if chunk_ids:
                        source_chunks.update(chunk_ids)

                # Deterministic path ID generation (stable across process restarts)
                node_seq = ",".join(nodes)
                path_id = f"path_{hashlib.md5(node_seq.encode('utf-8')).hexdigest()}"  # noqa: S324

                if path_id in seen_path_ids:
                    continue
                seen_path_ids.add(path_id)

                paths.append(
                    GraphPath(
                        path_id=path_id,
                        nodes=nodes,
                        edges=edges,
                        source_chunk_ids=tuple(sorted(source_chunks)),
                        confidence=1.0,
                    )
                )

            return paths


class AuthorizedGraphRetriever:
    """Graph traversal retriever with Relationship-Based Access Control (ReBAC) enforcement.

    Ensures that indirect information leakage is prevented by cutting traversal paths
    as soon as they encounter an unauthorized node or relationship.
    """

    def __init__(self, driver: AsyncDriver, fga: OpenFGAPolicyAdapter) -> None:
        self.driver = driver
        self.fga = fga
        self._raw_retriever = Neo4jGraphRetriever(driver)

    @staticmethod
    def _apply_scope_to_chunks(chunks: list[Chunk], scope: QueryScope) -> list[Chunk]:
        if scope.collections:
            raise QueryScopeError(
                "Query scope collections are not supported by graph source metadata."
            )
        return [
            chunk
            for chunk in chunks
            if (not scope.document_ids or chunk.document_id in scope.document_ids)
            and (
                scope.max_classification is None
                or chunk.classification.rank <= scope.max_classification.rank
            )
        ]

    async def traverse_authorized_subgraph(
        self,
        session: AsyncSession,
        subject: Subject,
        seed_entities: list[str],
        trace_id: str,
        max_depth: int = 2,
        scope: QueryScope | None = None,
    ) -> list[GraphPath]:
        """Fetch raw paths from Neo4j, apply batch FGA check on all source chunks.

        Enforce path-cut on unauthorized entities/relationships.
        """
        scope = scope or QueryScope()
        if scope.collections:
            raise QueryScopeError(
                "Query scope collections are not supported by graph source metadata."
            )

        # 1. Fetch raw paths from Neo4j
        raw_paths = await self._raw_retriever.traverse_subgraph(
            subject.tenant_id, seed_entities, max_depth=max_depth
        )

        if not raw_paths:
            return []

        # 2. Collect all unique edge IDs from raw paths
        all_edge_ids: set[str] = set()
        for path in raw_paths:
            all_edge_ids.update(path.edges)

        if not all_edge_ids:
            # Single-node paths (no edges) have no relationships to restrict.
            return raw_paths if scope.is_empty else []

        # 3. Retrieve chunks mapping for each edge from Neo4j
        edge_chunks: dict[str, list[str]] = {}
        cypher = """
        MATCH ()-[rel]->()
        WHERE elementId(rel) IN $edge_ids
        RETURN elementId(rel) AS edge_id, rel.source_chunk_ids AS chunk_ids
        """
        async with self.driver.session() as neo_session:
            result = await neo_session.run(cypher, edge_ids=list(all_edge_ids))
            async for record in result:
                e_id = record["edge_id"]
                c_ids = record["chunk_ids"] or []
                edge_chunks[e_id] = list(c_ids)

        # 4. Gather all unique chunk IDs across all edges
        all_chunk_ids = set()
        for chunk_list in edge_chunks.values():
            all_chunk_ids.update(chunk_list)

        # 5. Fetch full Chunk domain models from Postgres in a single batch
        chunks = await fetch_chunks_by_ids(session, subject.tenant_id, list(all_chunk_ids))
        chunks = self._apply_scope_to_chunks(chunks, scope)

        # 6. Filter chunks through OpenFGA
        authorized_chunks, decisions = await self.fga.filter_chunks(subject, chunks, trace_id)
        authorized_ids = {c.chunk_id for c in authorized_chunks}

        # 7. Persist decisions to Postgres
        await write_policy_decisions(session, decisions)

        # 8. Enforce Path-Cut: Prune path at first edge whose source chunks are unauthorized
        authorized_paths = []
        path_decisions: list[PolicyDecision] = []
        for path in raw_paths:
            path_valid = True
            for edge_id in path.edges:
                chunks_for_edge = edge_chunks.get(edge_id, [])
                # If an edge is supported ONLY by unauthorized chunks, the path is cut
                if chunks_for_edge and not any(cid in authorized_ids for cid in chunks_for_edge):
                    path_valid = False
                    break

            # Record the path-level outcome so the provenance report can prove
            # each emitted graph path was authorized (the chunk-level checks
            # above are recorded separately, but evidence rows reference the
            # path_id — without this record the explain view cannot match a
            # decision to the evidence and must treat it as denied).
            path_decisions.append(
                PolicyDecision(
                    decision_id=f"dec_{path.path_id}_{utcnow().timestamp()}",
                    trace_id=trace_id,
                    tenant_id=subject.tenant_id,
                    user_id=subject.user_id,
                    object_type=PolicyObjectType.GRAPH_PATH,
                    object_id=path.path_id,
                    decision=(
                        PolicyDecisionValue.ALLOW if path_valid else PolicyDecisionValue.DENY
                    ),
                    reason_code="graph_path_cut_pass" if path_valid else "graph_path_cut",
                )
            )

            if path_valid:
                # Reconstruct descriptive text context from the authorized chunk texts
                path_chunk_texts = []
                for cid in path.source_chunk_ids:
                    if cid in authorized_ids:
                        for c in authorized_chunks:
                            if c.chunk_id == cid:
                                path_chunk_texts.append(c.text)
                                break
                path_text = " ".join(path_chunk_texts)
                authorized_paths.append(path.model_copy(update={"text": path_text}))

        await write_policy_decisions(session, path_decisions)

        return authorized_paths
