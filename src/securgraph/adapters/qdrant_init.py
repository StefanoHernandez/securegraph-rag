"""Qdrant collection and indexing initialization.

Configures vectors, distance metrics, ReBAC payload indexes, andBM25 full-text indices.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models

from securgraph.config import get_settings


def init_qdrant_collection() -> None:
    """Initialize target Qdrant collection, payload schemas, and full-text indexes.

    Idempotent; does not overwrite or recreate the collection if it already exists.
    """
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)

    collections = client.get_collections().collections
    exists = any(c.name == settings.qdrant_collection for c in collections)

    if not exists:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=models.VectorParams(
                size=settings.embedding_dim,  # 1024
                distance=models.Distance.COSINE,
            ),
        )

        # Create Payload Indexes for fast ReBAC filtering and scoping
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name="tenant_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name="classification",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name="policy_tags",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )

        # Create Multilingual Full-Text index for native keyword matching
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name="text",
            field_schema=models.TextIndexParams(
                type="text",
                tokenizer=models.TokenizerType.MULTILINGUAL,
                lowercase=True,
            ),
        )
