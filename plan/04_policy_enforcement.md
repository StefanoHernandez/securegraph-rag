# Milestone 4: ReBAC Policy Enforcement Spec

This milestone integrates OpenFGA for Relationship-Based Access Control (ReBAC). It implements high-performance post-filtering for vector search candidates and path-cut authorization during graph traversals to ensure unauthorized content never enters the model context.

---

## 1. OpenFGA Authorization Model

The ReBAC schema enforces that users must have explicit permissions to view documents, chunks, and graph elements. Access to a `chunk` can be inherited from its parent `document`.

### 📝 OpenFGA DSL Schema (`docker/openfga/model.fga`)

```fga
model
  schema 1.1

type user

type group
  relations
    define member: [user, group#member]

type document
  relations
    define owner: [user]
    define group_viewer: [group#member]
    define direct_viewer: [user]
    define viewer: owner or group_viewer or direct_viewer

type chunk
  relations
    define parent_document: [document]
    define viewer: viewer from parent_document
```

---

## 2. Vector Candidate Post-Filtering

When dense/lexical search returns candidate chunks, we perform a parallel batch check using OpenFGA to filter out unauthorized elements *before* any ranking or context assembly occurs.

### 📝 Python Post-Filter Implementation (`src/securgraph/domain/policy.py`)

```python
from __future__ import annotations

import asyncio
from openfga_sdk import OpenFgaClient
from openfga_sdk.models import TupleKey, CheckRequest

from securgraph.domain import Subject, Chunk, PolicyDecision, PolicyDecisionValue, utcnow

class OpenFGAPolicyAdapter:
    def __init__(self, fga_client: OpenFgaClient) -> None:
        self.client = fga_client

    async def filter_chunks(
        self, subject: Subject, chunks: list[Chunk], trace_id: str
    ) -> tuple[list[Chunk], list[PolicyDecision]]:
        """Run batch authorization checks on chunks. Returns authorized chunks & decisions."""
        if not chunks:
            return [], []

        async def check_chunk(chunk: Chunk) -> tuple[Chunk, PolicyDecision]:
            # Construct check request: Can user Bob view chunk c_001?
            tuple_key = TupleKey(
                user=subject.fga_user,
                relation="viewer",
                object=f"chunk:{chunk.chunk_id}",
            )
            
            try:
                response = await self.client.check(CheckRequest(tuple_key=tuple_key))
                allowed = response.allowed or False
            except Exception as e:
                allowed = False  # Fail closed

            decision = PolicyDecision(
                decision_id=f"dec_{chunk.chunk_id}_{utcnow().timestamp()}",
                trace_id=trace_id,
                tenant_id=subject.tenant_id,
                user_id=subject.user_id,
                object_type="chunk",
                object_id=chunk.chunk_id,
                decision=PolicyDecisionValue.ALLOW if allowed else PolicyDecisionValue.DENY,
                reason_code="openfga_ok" if allowed else "unauthorized_rbac",
            )
            return chunk, decision

        # Run checks concurrently
        results = await asyncio.gather(*(check_chunk(c) for c in chunks))
        
        authorized_chunks = [chunk for chunk, dec in results if dec.decision == PolicyDecisionValue.ALLOW]
        all_decisions = [dec for _, dec in results]
        
        return authorized_chunks, all_decisions
```

---

## 3. Graph Traversal Path-Cut Enforcement

To prevent indirect information leakage, we must cut traversal paths as soon as they encounter an unauthorized node or relationship. For any path `A -> B -> C`, if the user has access to `A` and `C` but not `B`, the path must be pruned at `B`.

### 📝 Traversal Path-Cut Algorithm

```python
from neo4j import AsyncDriver
from securgraph.domain import Subject, GraphPath

class AuthorizedGraphRetriever:
    def __init__(self, driver: AsyncDriver, fga: OpenFGAPolicyAdapter) -> None:
        self.driver = driver
        self.fga = fga

    async def traverse_authorized_subgraph(
        self, subject: Subject, seed_entities: list[str], trace_id: str
    ) -> list[GraphPath]:
        # 1. Fetch raw paths from Neo4j
        raw_paths = await self.traverse_raw_paths(subject.tenant_id, seed_entities)
        
        authorized_paths = []
        for path in raw_paths:
            # 2. Extract all chunks linked to the edges along this path
            edge_chunks = await self.get_chunks_for_edges(path.edges)
            
            # 3. Check access for all source chunks on this path
            all_chunks = [c for chunks in edge_chunks.values() for c in chunks]
            authorized_chunks, decisions = await self.fga.filter_chunks(subject, all_chunks, trace_id)
            authorized_ids = {c.chunk_id for c in authorized_chunks}
            
            # 4. Enforce Path-Cut: Prune path at first edge whose source chunks are unauthorized
            path_valid = True
            for edge_id in path.edges:
                chunks_for_edge = edge_chunks.get(edge_id, [])
                # If an edge is supported ONLY by unauthorized chunks, the path is cut
                if chunks_for_edge and not any(c.chunk_id in authorized_ids for c in chunks_for_edge):
                    path_valid = False
                    break
            
            if path_valid:
                authorized_paths.append(path)
                
        return authorized_paths
```

---

## 4. Security Audit Trail

All authorization denials are captured in structural format and written to the structured logs & PostgreSQL provenance trace. These audit logs record:
- The exact user `subject.fga_user`.
- The resource `chunk_id` / `document_id` that was denied.
- The trace identifier linking the denial back to the specific query query.
- System fails closed in the event of an OpenFGA timeout.
