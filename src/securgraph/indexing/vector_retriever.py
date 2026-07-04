"""Qdrant retriever for dense semantic and lexical keyword retrieval.

Implements multi-tenant scoped searches and maps results to domain-compliant evidence records.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models

from securgraph.adapters.embeddings import EmbeddingAdapter
from securgraph.config import get_settings
from securgraph.domain import RetrievalEvidence, RetrieverId


class QdrantRetriever:
    """Retriever for Qdrant executing dense and lexical search strategies.

    All queries are strictly filtered by the caller's tenant_id to guarantee multi-tenancy.
    """

    def __init__(self, client: QdrantClient, embeddings: EmbeddingAdapter) -> None:
        self._client = client
        self._embeddings = embeddings
        self._collection = get_settings().qdrant_collection

    async def search_dense(
        self,
        tenant_id: str,
        query: str,
        *,
        limit: int = 10,
        trace_id: str | None = None,
    ) -> list[RetrievalEvidence]:
        """Perform a dense semantic vector search for the query, filtered by tenant_id."""
        trace = trace_id or "trace_system"
        query_vector = self._embeddings.embed_text(query)

        # Force strict scoping for multi-tenancy
        tenant_filter = models.Filter(
            must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id))]
        )

        response = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=tenant_filter,
            limit=limit,
        )
        results = response.points

        return [
            RetrievalEvidence(
                evidence_id=f"ev_dense_{r.id}_{trace}",
                trace_id=trace,
                tenant_id=tenant_id,
                evidence_type="chunk",
                object_id=r.payload["chunk_id"],  # type: ignore
                retriever_id=RetrieverId.VECTOR,
                retrieval_strategy="dense",
                original_rank=idx,
                original_score=r.score,
                policy_check_id="unverified",
                text=r.payload.get("text"),  # type: ignore
                document_id=r.payload.get("document_id"),  # type: ignore
            )
            for idx, r in enumerate(results)
        ]

    async def search_lexical(
        self,
        tenant_id: str,
        query: str,
        *,
        limit: int = 10,
        trace_id: str | None = None,
    ) -> list[RetrievalEvidence]:
        """Perform a lexical keyword search using Qdrant's multilingual full-text index."""
        trace = trace_id or "trace_system"

        # Filter: match tenant_id AND perform full-text match on the text field
        lexical_filter = models.Filter(
            must=[
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                models.FieldCondition(key="text", match=models.MatchText(text=query)),
            ]
        )

        # Scrolling returns points matching the filter.
        # We scroll up to limit items. Unsorted in Qdrant's native scroll, scored in fusion.
        scroll_results = self._client.scroll(
            collection_name=self._collection,
            scroll_filter=lexical_filter,
            limit=limit,
            with_payload=True,
        )[0]

        return [
            RetrievalEvidence(
                evidence_id=f"ev_lex_{r.id}_{trace}",
                trace_id=trace,
                tenant_id=tenant_id,
                evidence_type="chunk",
                object_id=r.payload["chunk_id"],  # type: ignore
                retriever_id=RetrieverId.LEXICAL,
                retrieval_strategy="full_text",
                original_rank=idx,
                original_score=1.0,  # Uniform default score before RRF fusion
                policy_check_id="unverified",
                text=r.payload.get("text"),  # type: ignore
                document_id=r.payload.get("document_id"),  # type: ignore
            )
            for idx, r in enumerate(scroll_results)
        ]
