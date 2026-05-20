"""Unit tests for knowledge graph domain, adapters, and retrieval.

Exercises the Neo4j adapters, Ollama extraction client, path retriever, and
the ingestion pipeline integration.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j import AsyncDriver

from securgraph.adapters.neo4j import (
    close_neo4j_driver,
    get_neo4j_driver,
    reset_neo4j_driver_cache,
)
from securgraph.adapters.neo4j_writer import write_graph_slice
from securgraph.adapters.ollama import extract_graph_slice, get_ollama_client
from securgraph.domain import Chunk, Classification
from securgraph.domain.graph import (
    ExtractedEntity,
    ExtractedGraphSlice,
    ExtractedRelation,
    GraphPath,
)
from securgraph.indexing.graph_retriever import Neo4jGraphRetriever
from securgraph.ingestion.service import IngestionRequest, ingest_document

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------------
# 1. Domain Model Tests
# ----------------------------------------------------------------------------


def test_extracted_graph_slice_extra_fields_tolerance() -> None:
    """Verify ExtractedGraphSlice handles extra LLM output fields without crashing."""
    raw_data = {
        "entities": [
            {"name": "Server1", "label": "Server", "extra_field": "ignore_me"},
            {"name": "DB1", "label": "Database"},
        ],
        "relations": [
            {
                "source_entity_name": "Server1",
                "target_entity_name": "DB1",
                "relationship_type": "CONNECTS_TO",
                "confidence": 0.95,
                "ignored_key": 42,
            }
        ],
        "unexpected_top_level_field": "still_ok",
    }

    parsed = ExtractedGraphSlice.model_validate(raw_data)
    assert len(parsed.entities) == 2
    assert parsed.entities[0].name == "Server1"
    assert parsed.entities[0].label == "Server"
    assert len(parsed.relations) == 1
    assert parsed.relations[0].relationship_type == "CONNECTS_TO"
    assert parsed.relations[0].confidence == 0.95


def test_graph_path_validation() -> None:
    """Verify GraphPath enforces len(edges) == len(nodes) - 1 invariant."""
    # Valid: 3 nodes, 2 edges
    path_ok = GraphPath(
        path_id="path_123",
        nodes=("nodeA", "nodeB", "nodeC"),
        edges=("edge1", "edge2"),
        source_chunk_ids=("chk_1",),
        confidence=1.0,
    )
    assert path_ok.path_id == "path_123"

    # Valid: 1 node, 0 edges
    single_node_path = GraphPath(
        path_id="path_single",
        nodes=("nodeA",),
        edges=(),
        source_chunk_ids=(),
        confidence=1.0,
    )
    assert len(single_node_path.edges) == 0

    # Invalid: 2 nodes, 2 edges (should raise ValueError)
    with pytest.raises(ValueError, match="edges count must equal nodes-1"):
        GraphPath(
            path_id="path_invalid",
            nodes=("nodeA", "nodeB"),
            edges=("edge1", "edge2"),
            source_chunk_ids=(),
            confidence=1.0,
        )


# ----------------------------------------------------------------------------
# 2. Connection Manager Tests
# ----------------------------------------------------------------------------


def test_neo4j_connection_manager_singleton() -> None:
    """Verify get_neo4j_driver behaves as a process-wide singleton."""
    reset_neo4j_driver_cache()

    with patch("neo4j.AsyncGraphDatabase.driver") as mock_driver_fn:
        mock_driver = MagicMock(spec=AsyncDriver)
        mock_driver_fn.return_value = mock_driver

        driver1 = get_neo4j_driver()
        driver2 = get_neo4j_driver()

        # Should only invoke the builder once due to lru_cache
        mock_driver_fn.assert_called_once()
        assert driver1 is driver2


@pytest.mark.asyncio
async def test_neo4j_connection_manager_closure() -> None:
    """Verify close_neo4j_driver disposes the driver and clears the cache."""
    reset_neo4j_driver_cache()

    with patch("neo4j.AsyncGraphDatabase.driver") as mock_driver_fn:
        mock_driver = MagicMock(spec=AsyncDriver)
        mock_driver.close = AsyncMock()
        mock_driver_fn.return_value = mock_driver

        _ = get_neo4j_driver()
        await close_neo4j_driver()

        mock_driver.close.assert_called_once()

        # Re-fetching driver should rebuild a new instance
        _ = get_neo4j_driver()
        assert mock_driver_fn.call_count == 2


# ----------------------------------------------------------------------------
# 3. Ollama Client Extraction Adapter Tests
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_client_singleton() -> None:
    """Verify get_ollama_client caches client instance."""

    client1 = get_ollama_client()
    client2 = get_ollama_client()
    assert client1 is client2


@pytest.mark.asyncio
async def test_extract_graph_slice_success() -> None:
    """Verify clean JSON responses from Ollama are correctly parsed."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.message.content = json.dumps(
        {
            "entities": [{"name": "Neo4j", "label": "Database"}],
            "relations": [
                {
                    "source_entity_name": "SecureGraph",
                    "target_entity_name": "Neo4j",
                    "relationship_type": "STORES_DATA_IN",
                    "confidence": 0.98,
                }
            ],
        }
    )
    mock_client.chat.return_value = mock_response

    with patch("securgraph.adapters.ollama.get_ollama_client", return_value=mock_client):
        slice_res = await extract_graph_slice("Some text...")
        assert len(slice_res.entities) == 1
        assert slice_res.entities[0].name == "Neo4j"
        assert len(slice_res.relations) == 1
        assert slice_res.relations[0].relationship_type == "STORES_DATA_IN"


@pytest.mark.asyncio
async def test_extract_graph_slice_with_markdown_fences() -> None:
    """Verify markdown code block wrappers are stripped successfully from LLM output."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.message.content = (
        "```json\n"
        "{\n"
        '  "entities": [{"name": "Python", "label": "Language"}],\n'
        '  "relations": []\n'
        "}\n"
        "```"
    )
    mock_client.chat.return_value = mock_response

    with patch("securgraph.adapters.ollama.get_ollama_client", return_value=mock_client):
        slice_res = await extract_graph_slice("Some text...")
        assert len(slice_res.entities) == 1
        assert slice_res.entities[0].name == "Python"
        assert len(slice_res.relations) == 0


@pytest.mark.asyncio
async def test_extract_graph_slice_error_fallback() -> None:
    """Verify extraction failure returns empty slice fallback gracefully."""
    mock_client = AsyncMock()
    mock_client.chat.side_effect = Exception("Ollama connection timed out")

    with patch("securgraph.adapters.ollama.get_ollama_client", return_value=mock_client):
        slice_res = await extract_graph_slice("Some text...")
        # Graceful empty recovery
        assert len(slice_res.entities) == 0
        assert len(slice_res.relations) == 0


# ----------------------------------------------------------------------------
# 4. Neo4j Writer Tests
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_neo4j_writer_runs_transactional_cypher() -> None:
    """Verify write_graph_slice executes atomic write transaction block."""
    mock_driver = MagicMock(spec=AsyncDriver)
    mock_session = AsyncMock()
    mock_tx = AsyncMock()

    mock_driver.session.return_value.__aenter__.return_value = mock_session

    from collections.abc import Callable
    from typing import Any

    async def side_effect_write(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await fn(mock_tx, *args, **kwargs)

    mock_session.execute_write.side_effect = side_effect_write

    chunk = Chunk(
        chunk_id="chk_abc",
        document_id="doc_xyz",
        tenant_id="tenant_1",
        text="A simple text about Python.",
        classification=Classification.INTERNAL,
        policy_tags=("dev",),
        token_count=10,
        ordinal=0,
        text_hash="hash1234",
    )

    slice_data = ExtractedGraphSlice(
        entities=[ExtractedEntity(name="Python", label="Language")],
        relations=[
            ExtractedRelation(
                source_entity_name="SecureGraph",
                target_entity_name="Python",
                relationship_type="WRITTEN_IN",
                confidence=1.0,
            )
        ],
    )

    await write_graph_slice(mock_driver, "tenant_1", chunk, slice_data)

    # Asserts execute_write was run
    mock_session.execute_write.assert_called_once()

    # Asserts Cypher queries executed within transaction boundary
    assert mock_tx.run.call_count == 3

    # Verify the chunk/doc MERGE call
    args_call_1 = mock_tx.run.call_args_list[0]
    query_1 = args_call_1[0][0]
    assert "MERGE (d:Document" in query_1
    assert "MERGE (c:Chunk" in query_1
    assert args_call_1[1]["doc_id"] == "doc_xyz"
    assert args_call_1[1]["classification"] == "internal"

    # Verify the entities UNWIND call
    args_call_2 = mock_tx.run.call_args_list[1]
    query_2 = args_call_2[0][0]
    assert "UNWIND $entities" in query_2
    assert "MERGE (e:Entity" in query_2
    assert args_call_2[1]["entities"] == [{"name": "Python", "label": "Language"}]

    # Verify the relations UNWIND call (checking pure-Cypher no-APOC deduplication logic)
    args_call_3 = mock_tx.run.call_args_list[2]
    query_3 = args_call_3[0][0]
    assert "UNWIND $relations" in query_3
    assert "MERGE (src)-[r:RELATED_TO" in query_3
    assert "coalesce(r.source_chunk_ids, []) + [$chunk_id]" in query_3
    assert args_call_3[1]["chunk_id"] == "chk_abc"


# ----------------------------------------------------------------------------
# 5. Graph Retriever Traversal Tests
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graph_retriever_empty_seeds() -> None:
    """Verify empty seed entities list returns empty traversal immediately."""
    mock_driver = MagicMock(spec=AsyncDriver)
    retriever = Neo4jGraphRetriever(mock_driver)

    paths = await retriever.traverse_subgraph("tenant_1", [])
    assert paths == []
    mock_driver.session.assert_not_called()


@pytest.mark.asyncio
async def test_graph_retriever_depth_boundary() -> None:
    """Verify max_depth parameters are safely restricted between 1 and 4."""
    mock_driver = MagicMock(spec=AsyncDriver)
    mock_session = AsyncMock()
    mock_result = AsyncMock()

    mock_driver.session.return_value.__aenter__.return_value = mock_session
    mock_session.run.return_value = mock_result

    # Mocking record generator yielding nothing
    from collections.abc import AsyncIterator
    from typing import Any

    async def mock_async_iter() -> AsyncIterator[Any]:
        if False:
            yield None

    mock_result.__aiter__.side_effect = mock_async_iter

    retriever = Neo4jGraphRetriever(mock_driver)

    # 1. Depth too high (5 should cap at 4)
    await retriever.traverse_subgraph("tenant_1", ["Node1"], max_depth=5)
    cypher_high = mock_session.run.call_args[0][0]
    assert "RELATED_TO*1..4" in cypher_high

    # 2. Depth too low (0 should cap at 1)
    await retriever.traverse_subgraph("tenant_1", ["Node1"], max_depth=0)
    cypher_low = mock_session.run.call_args[0][0]
    assert "RELATED_TO*1..1" in cypher_low


@pytest.mark.asyncio
async def test_graph_retriever_parses_records_with_md5_id() -> None:
    """Verify records are converted to GraphPath instances with stable MD5 hashes."""
    mock_driver = MagicMock(spec=AsyncDriver)
    mock_session = AsyncMock()
    mock_result = AsyncMock()

    mock_driver.session.return_value.__aenter__.return_value = mock_session
    mock_session.run.return_value = mock_result

    # Yield mock records representing a traversed path: A -> B
    mock_records = [
        {
            "path_nodes": [
                {"id": "A", "label": "Entity", "name": "A"},
                {"id": "B", "label": "Entity", "name": "B"},
            ],
            "path_edges": [
                {
                    "id": "rel_ab",
                    "source": "A",
                    "target": "B",
                    "type": "DEPENDS_ON",
                    "source_chunk_ids": ["chk_2", "chk_1"],
                }
            ],
        }
    ]

    from collections.abc import AsyncIterator
    from typing import Any

    async def mock_async_iter() -> AsyncIterator[Any]:
        for r in mock_records:
            yield r

    mock_result.__aiter__.side_effect = mock_async_iter

    retriever = Neo4jGraphRetriever(mock_driver)
    paths = await retriever.traverse_subgraph("tenant_1", ["A"])

    assert len(paths) == 1
    path = paths[0]

    # Path validation invariants
    assert path.nodes == ("A", "B")
    assert path.edges == ("rel_ab",)

    # Aggregated source chunk IDs should be sorted and unique
    assert path.source_chunk_ids == ("chk_1", "chk_2")

    # Path ID must be deterministic hash of the nodes sequence "A,B"
    import hashlib

    expected_hash = hashlib.md5(b"A,B").hexdigest()  # noqa: S324
    assert path.path_id == f"path_{expected_hash}"


# ----------------------------------------------------------------------------
# 6. Ingestion Pipeline Integration Test
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_document_triggers_graph_processing() -> None:
    """Verify ingest_document invokes extraction and writing for each chunk."""
    from sqlalchemy.ext.asyncio import AsyncSession

    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.begin.return_value = AsyncMock()

    # Mocking standard DB dependencies
    with (
        patch("securgraph.ingestion.service.find_existing_document", return_value=None),
        patch("securgraph.ingestion.service.create_ingestion_job"),
        patch("securgraph.ingestion.service.update_ingestion_job"),
        patch("securgraph.ingestion.service.upsert_document"),
        patch("securgraph.ingestion.service.upsert_chunks"),
        patch("securgraph.ingestion.service.write_ingestion_provenance"),
        patch("securgraph.indexing.qdrant_writer.upsert_chunks_to_qdrant"),
        patch("securgraph.adapters.embeddings.get_embedding_adapter"),
        patch("securgraph.adapters.ollama.extract_graph_slice") as mock_extract,
        patch("securgraph.adapters.neo4j_writer.write_graph_slice") as mock_write,
    ):
        mock_extract.return_value = ExtractedGraphSlice(
            entities=[ExtractedEntity(name="ProjectA", label="Project")],
            relations=[],
        )

        request = IngestionRequest(
            tenant_id="tenant_1",
            source_uri="file://test_project.txt",
            owner_id="user:stefano",
            data=b"SecureGraph is an awesome local RAG ProjectA.",
            classification=Classification.INTERNAL,
        )

        res = await ingest_document(mock_session, request, neo4j_driver=MagicMock())

        # Verify success
        assert res.chunks_created == 1

        # Extractor and Neo4j writer should be called exactly once (since we had 1 chunk)
        mock_extract.assert_called_once_with("SecureGraph is an awesome local RAG ProjectA.")
        mock_write.assert_called_once()
