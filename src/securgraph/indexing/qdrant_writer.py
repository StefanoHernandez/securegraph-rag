"""Qdrant writer for chunk vector indexing.

Encapsulates upserts of dense chunk embeddings and ReBAC/multi-tenant payloads to Qdrant.
"""

from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.http import models

from securgraph.adapters.embeddings import EmbeddingAdapter
from securgraph.config import get_settings
from securgraph.domain import Chunk


def upsert_chunks_to_qdrant(
    client: QdrantClient,
    chunks: list[Chunk],
    embeddings: EmbeddingAdapter,
    *,
    source_uri: str | None = None,
) -> None:
    """Batch embed and upsert chunks to Qdrant.

    Computes vectors for chunks using the supplied EmbeddingAdapter, packages
    them as PointStruct objects, and upserts them to the configured collection.
    """
    if not chunks:
        return

    settings = get_settings()

    # 1. Batch generate dense embeddings
    texts = [c.text for c in chunks]
    vectors = embeddings.embed_chunks(texts)

    # 2. Package points
    points = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        # Derive a stable namespace-based UUID for Qdrant from the string chunk_id
        point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk.chunk_id))

        payload = {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "tenant_id": chunk.tenant_id,
            "text": chunk.text,
            "classification": chunk.classification.value,
            "policy_tags": list(chunk.policy_tags),
        }
        if source_uri:
            payload["source_uri"] = source_uri

        points.append(
            models.PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            )
        )

    # 3. Upsert points into the collection
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=points,
        wait=True,
    )


def delete_chunks_from_qdrant(client: QdrantClient, chunk_ids: list[str]) -> None:
    """Delete exactly the stable Qdrant points created for an ingest attempt."""
    if not chunk_ids:
        return
    settings = get_settings()
    point_ids = [str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id)) for chunk_id in chunk_ids]
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.PointIdsList(points=point_ids),
        wait=True,
    )
