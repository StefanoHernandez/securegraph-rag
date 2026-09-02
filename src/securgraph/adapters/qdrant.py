"""Qdrant client construction.

Single place where a ``QdrantClient`` is built, so the API key from Settings
can never be forgotten at one of the call sites (retrieval, ingestion, health,
admin atlas, workers, eval harness).
"""

from __future__ import annotations

from typing import Any

from qdrant_client import QdrantClient

from securgraph.config import Settings, get_settings


def build_qdrant_client(settings: Settings | None = None, **kwargs: Any) -> QdrantClient:
    """Return a QdrantClient pointed at the configured URL, authenticated.

    ``api_key`` is omitted when empty so a local unauthenticated dev instance
    still works; stage/prod refuse to boot without one.
    """
    resolved = settings or get_settings()
    api_key = resolved.qdrant_api_key.get_secret_value()
    return QdrantClient(
        url=resolved.qdrant_url,
        api_key=api_key or None,
        **kwargs,
    )
