# Milestone 2: Vector & Lexical Retrieval Spec

This milestone configures the vector database (Qdrant), integrates the local `BAAI/bge-m3` embedding model, and implements dense vector search along with lexical text matching inside Qdrant.

---

## 1. Qdrant Collection Setup

We will configure a single collection named `securgraph_chunks` inside Qdrant. 
- Vector size: `1024` (dimensions of `BAAI/bge-m3` embeddings).
- Distance metric: `Cosine`.
- Payload indexing: Index `tenant_id`, `classification`, and `policy_tags` fields for fast metadata-level filtering.
- Full-text indexing: Index the `text` field with standard text match configuration for native lexical search.

### 📝 Collection Creation script (`src/securgraph/adapters/qdrant_init.py`)

```python
from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models
from securgraph.config import get_settings

def init_qdrant_collection() -> None:
    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    
    # Check if collection exists
    collections = client.get_collections().collections
    exists = any(c.name == settings.qdrant_collection for c in collections)
    
    if not exists:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=models.VectorParams(
                size=settings.embedding_dim,  # 1024
                distance=models.Distance.COSINE,
            )
        )
        # Create Payload Indexes for fast ReBAC scoping & multi-tenancy
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
        # Create Full-Text index for native BM25-equivalent lexical matching
        client.create_payload_index(
            collection_name=settings.qdrant_collection,
            field_name="text",
            field_schema=models.TextIndexParams(
                type="text",
                tokenizer=models.TokenizerType.MULTILINGUAL,
                lowercase=True,
                cleansed=True,
            )
        )
```

---

## 2. Embedding Adapter (`FlagEmbedding` with `BAAI/bge-m3`)

The embedding adapter provides a clean, abstract interface around the heavy deep learning model. It supports Apple Silicon GPU acceleration (`mps`), CUDA, and CPU fallback.

### 📝 Python Adapter (`src/securgraph/adapters/embeddings.py`)

```python
from __future__ import annotations

import torch
from abc import ABC, abstractmethod
from FlagEmbedding import BGEM3FlagModel
from securgraph.config import get_settings

class EmbeddingAdapter(ABC):
    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Generate vector embedding for a single query string."""
        pass

    @abstractmethod
    def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        """Generate batch vector embeddings for a list of document chunks."""
        pass

class BGEM3EmbeddingAdapter(EmbeddingAdapter):
    def __init__(self) -> None:
        settings = get_settings()
        # Auto-detect MPS (Mac GPU), CUDA (Nvidia GPU), or CPU fallback
        if settings.embedding_device == "auto":
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        else:
            device = settings.embedding_device

        self.model = BGEM3FlagModel(
            settings.embedding_model,
            use_fp16=(device in {"cuda", "mps"}),
            device=device,
        )

    def embed_text(self, text: str) -> list[float]:
        res = self.model.encode(text, return_dense=True)
        return res["dense"].tolist()

    def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        res = self.model.encode(chunks, return_dense=True)
        return res["dense"].tolist()
```

---

## 3. Retrieval Engine Contracts

We will implement `src/securgraph/indexing/vector_retriever.py` to retrieve dense and lexical candidates from Qdrant.

### 📝 Retrieval Query Implementations

```python
from qdrant_client import QdrantClient
from qdrant_client.http import models
from securgraph.domain import RetrieverId, RetrievalEvidence

class QdrantRetriever:
    def __init__(self, client: QdrantClient, embeddings: EmbeddingAdapter) -> None:
        self.client = client
        self.embeddings = embeddings
        self.collection = get_settings().qdrant_collection

    async def search_dense(
        self, tenant_id: str, query: str, limit: int = 10
    ) -> list[RetrievalEvidence]:
        query_vector = self.embeddings.embed_text(query)
        
        # Scoped filter for multi-tenancy
        tenant_filter = models.Filter(
            must=[models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id))]
        )
        
        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            query_filter=tenant_filter,
            limit=limit,
        )
        
        return [
            RetrievalEvidence(
                evidence_id=f"ev_dense_{r.id}",
                trace_id="",
                tenant_id=tenant_id,
                evidence_type="chunk",
                object_id=r.payload["chunk_id"],
                retriever_id=RetrieverId.VECTOR,
                retrieval_strategy="dense",
                original_rank=idx,
                original_score=r.score,
            )
            for idx, r in enumerate(results)
        ]

    async def search_lexical(
        self, tenant_id: str, term: str, limit: int = 10
    ) -> list[RetrievalEvidence]:
        # Native Qdrant Full-Text Keyword Query
        lexical_filter = models.Filter(
            must=[
                models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                models.FieldCondition(key="text", match=models.MatchText(text=term))
            ]
        )
        
        results = self.client.scroll(
            collection_name=self.collection,
            scroll_filter=lexical_filter,
            limit=limit,
            with_payload=True,
        )[0]
        
        return [
            RetrievalEvidence(
                evidence_id=f"ev_lex_{r.id}",
                trace_id="",
                tenant_id=tenant_id,
                evidence_type="chunk",
                object_id=r.payload["chunk_id"],
                retriever_id=RetrieverId.LEXICAL,
                retrieval_strategy="full_text",
                original_rank=idx,
                original_score=1.0,  # scrolling returns unsorted, scored via post-processing or fusion
            )
            for idx, r in enumerate(results)
        ]
```
