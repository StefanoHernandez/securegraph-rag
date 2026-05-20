"""Integration and unit tests for the retrieval pipeline.

Tests collection initialization, embedding extraction mock, chunk indexing,
and dual dense/lexical vector retriever with tenant isolation.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Generator

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models

from securgraph.adapters.embeddings import EmbeddingAdapter
from securgraph.adapters.qdrant_init import init_qdrant_collection
from securgraph.config import get_settings
from securgraph.domain import Chunk, Classification, RetrieverId
from securgraph.indexing.qdrant_writer import upsert_chunks_to_qdrant
from securgraph.indexing.vector_retriever import QdrantRetriever


class MockEmbeddingAdapter(EmbeddingAdapter):
    """Deterministic, lightning-fast mock embedding adapter."""

    def embed_text(self, text: str) -> list[float]:
        import hashlib
        # Generate deterministic vector of dimension 1024
        h = hashlib.sha256(text.encode()).digest()
        floats = []
        for idx in range(1024):
            val = (h[idx % len(h)] * (idx + 1)) % 1000 / 500.0 - 1.0
            floats.append(val)
        return floats

    def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        return [self.embed_text(c) for c in chunks]


@pytest.fixture(autouse=True)
def test_qdrant_collection(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Isolate and clean up Qdrant test collection."""
    monkeypatch.setenv("SECURGRAPH_QDRANT_COLLECTION", "test_securgraph_chunks")
    monkeypatch.setenv("SECURGRAPH_QDRANT_URL", "http://127.0.0.1:6333")
    # Reset config cached settings so env overrides take effect
    get_settings.cache_clear()

    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)

    # Pre-test cleanup
    with contextlib.suppress(Exception):
        client.delete_collection("test_securgraph_chunks")

    yield

    # Post-test cleanup
    with contextlib.suppress(Exception):
        client.delete_collection("test_securgraph_chunks")


def test_qdrant_collection_initialization() -> None:
    """Verify collection initialization creates correct schemas and payload indexes."""
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)

    # Collection shouldn't exist initially
    collections = client.get_collections().collections
    assert not any(c.name == "test_securgraph_chunks" for c in collections)

    # Initialize collection
    init_qdrant_collection()

    # Collection should now exist
    collections = client.get_collections().collections
    assert any(c.name == "test_securgraph_chunks" for c in collections)

    # Check payload indexes
    col_info = client.get_collection("test_securgraph_chunks")
    payload_schema = col_info.payload_schema
    assert "tenant_id" in payload_schema
    assert payload_schema["tenant_id"].data_type == models.PayloadSchemaType.KEYWORD
    assert "classification" in payload_schema
    assert payload_schema["classification"].data_type == models.PayloadSchemaType.KEYWORD
    assert "policy_tags" in payload_schema
    assert payload_schema["policy_tags"].data_type == models.PayloadSchemaType.KEYWORD
    assert "text" in payload_schema
    # Text index params representation in client
    assert payload_schema["text"].data_type == models.PayloadSchemaType.TEXT


@pytest.mark.asyncio
async def test_qdrant_writer_and_dual_strategy_retriever() -> None:
    """Verify upsert_chunks_to_qdrant and QdrantRetriever dense/lexical search behaviors."""
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    mock_embeddings = MockEmbeddingAdapter()

    # Initialize collection
    init_qdrant_collection()

    # Create dummy chunks across tenants
    chunks = [
        Chunk(
            chunk_id="chk_apple",
            document_id="doc_1",
            tenant_id="tenant_a",
            text="The secret Apple Pie recipe includes cinnamon and sugar.",
            classification=Classification.RESTRICTED,
            policy_tags=("bakers_guild",),
            token_count=10,
            ordinal=0,
            text_hash="dummyhash1",
        ),
        Chunk(
            chunk_id="chk_banana",
            document_id="doc_2",
            tenant_id="tenant_b",
            text="Banana splits require ripe yellow bananas and fresh vanilla ice cream.",
            classification=Classification.INTERNAL,
            policy_tags=("chefs_guild",),
            token_count=12,
            ordinal=1,
            text_hash="dummyhash2",
        ),
    ]

    # 1. Test Upsert
    upsert_chunks_to_qdrant(client, chunks, mock_embeddings)

    # Verify points were indexed with stable namespace-based UUID5 mapping
    points_res = client.scroll(
        collection_name="test_securgraph_chunks",
        with_vectors=True,
    )[0]
    assert len(points_res) == 2

    # Verify ID is a stable UUID5
    expected_apple_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "chk_apple"))
    expected_banana_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, "chk_banana"))
    point_ids = {p.id for p in points_res}
    assert expected_apple_uuid in point_ids
    assert expected_banana_uuid in point_ids

    # 2. Test Retriever
    retriever = QdrantRetriever(client, mock_embeddings)

    # --- Dense Search Tests ---
    # Query for "Apple Pie" in tenant_a -> should return the Apple Pie chunk
    evidences_a = await retriever.search_dense(
        tenant_id="tenant_a",
        query="Apple Pie",
        limit=5,
    )
    assert len(evidences_a) == 1
    assert evidences_a[0].object_id == "chk_apple"
    assert evidences_a[0].retriever_id == RetrieverId.VECTOR
    assert evidences_a[0].retrieval_strategy == "dense"
    assert evidences_a[0].policy_check_id == "unverified"
    assert evidences_a[0].tenant_id == "tenant_a"

    # Multi-tenancy check: Query for "Apple Pie" in tenant_b -> should return nothing from tenant_a
    evidences_b = await retriever.search_dense(
        tenant_id="tenant_b",
        query="Apple Pie",
        limit=5,
    )
    assert "chk_apple" not in [e.object_id for e in evidences_b]
    assert all(e.tenant_id == "tenant_b" for e in evidences_b)

    # --- Lexical Keyword Search Tests ---
    # Lexical query for "bananas" in tenant_b -> should match the Banana split chunk
    lexical_evidences_b = await retriever.search_lexical(
        tenant_id="tenant_b",
        query="bananas",
        limit=5,
    )
    assert len(lexical_evidences_b) == 1
    assert lexical_evidences_b[0].object_id == "chk_banana"
    assert lexical_evidences_b[0].retriever_id == RetrieverId.LEXICAL
    assert lexical_evidences_b[0].retrieval_strategy == "full_text"
    assert lexical_evidences_b[0].policy_check_id == "unverified"
    assert lexical_evidences_b[0].tenant_id == "tenant_b"

    # Multi-tenancy check: Lexical query for "bananas" in tenant_a -> should return nothing
    lexical_evidences_a = await retriever.search_lexical(
        tenant_id="tenant_a",
        query="bananas",
        limit=5,
    )
    assert len(lexical_evidences_a) == 0
