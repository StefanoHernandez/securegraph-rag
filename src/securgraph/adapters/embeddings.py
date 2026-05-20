"""Embedding adapter module.

Provides a clean abstract interface around local or remote embedding generation models.
Includes thread-safe cached singleton and GPU acceleration auto-detection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import torch
from FlagEmbedding import BGEM3FlagModel

from securgraph.config import get_settings


class EmbeddingAdapter(ABC):
    """Abstract base class for all vector embedding models."""

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Generate dense vector embedding for a single text query."""
        pass

    @abstractmethod
    def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        """Generate dense vector embeddings for a list of text chunks in batch."""
        pass


class BGEM3EmbeddingAdapter(EmbeddingAdapter):
    """Concrete adapter for the local BAAI/bge-m3 model.

    Auto-detects Apple Silicon (MPS), CUDA (NVIDIA), or CPU execution context.
    """

    def __init__(self) -> None:
        settings = get_settings()
        device = settings.embedding_device

        if device == "auto":
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"

        # FP16 precision is only supported and recommended for GPUs (MPS or CUDA)
        use_fp16 = device in {"cuda", "mps"}

        self._model = BGEM3FlagModel(
            settings.embedding_model,
            use_fp16=use_fp16,
            device=device,
        )

    def embed_text(self, text: str) -> list[float]:
        """Generate dense vector embedding for a query string."""
        res = self._model.encode(text, return_dense=True)
        vecs = res["dense_vecs"]
        # Handle potential 2D vs 1D return format from BGE-M3
        if hasattr(vecs, "ndim") and vecs.ndim == 2:
            return vecs[0].tolist()  # type: ignore
        elif isinstance(vecs, list) and vecs and isinstance(vecs[0], list):
            return vecs[0]
        return vecs.tolist()  # type: ignore

    def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        """Generate batch dense vector embeddings for a list of chunks."""
        if not chunks:
            return []
        res = self._model.encode(chunks, return_dense=True)
        return res["dense_vecs"].tolist()  # type: ignore


# Thread-safe module level singleton reference
_ADAPTER: EmbeddingAdapter | None = None


def get_embedding_adapter() -> EmbeddingAdapter:
    """Get the active cached EmbeddingAdapter singleton."""
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = BGEM3EmbeddingAdapter()
    return _ADAPTER
