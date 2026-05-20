"""Neo4j graph traversal retriever.

Traverses entity relationships to retrieve semantic graph paths.
"""

from __future__ import annotations

import hashlib

from neo4j import AsyncDriver

from securgraph.domain.graph import GraphPath


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
