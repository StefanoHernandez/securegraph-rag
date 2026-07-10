"""Unit tests for vector projection and Vector Atlas service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from securgraph.indexing.vector_atlas import build_vector_atlas, chunk_id_to_point_id
from securgraph.indexing.vector_projection import project_mds_from_pairs, project_pca

pytestmark = pytest.mark.unit


def test_project_pca_reduces_to_two_dimensions() -> None:
    rng = np.random.default_rng(0)
    vectors = rng.standard_normal((8, 16)).tolist()
    coords = project_pca(vectors, dims=2)
    assert len(coords) == 8
    assert all(len(row) == 2 for row in coords)


def test_project_mds_from_pairs_returns_coordinates() -> None:
    ids = ["a", "b", "c"]
    pairs = [("a", "b", 0.9), ("b", "c", 0.8), ("a", "c", 0.7)]
    coords = project_mds_from_pairs(ids, pairs, dims=2)
    assert set(coords) == set(ids)
    assert all(len(coords[pid]) == 2 for pid in ids)


def test_chunk_id_to_point_id_is_stable() -> None:
    first = chunk_id_to_point_id("chunk_abc")
    second = chunk_id_to_point_id("chunk_abc")
    assert first == second


def test_project_pca_supports_three_dimensions() -> None:
    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 1.0, 1.0]]
    coords = project_pca(vectors, dims=3)
    assert len(coords) == 4
    assert all(len(row) == 3 for row in coords)


@pytest.mark.asyncio
async def test_build_vector_atlas_enriches_metadata() -> None:
    mock_client = MagicMock()
    mock_client.scroll.return_value = (
        [
            MagicMock(
                id="point-1",
                vector=[1.0, 0.0, 0.0],
                payload={
                    "chunk_id": "chunk_1",
                    "document_id": "doc_1",
                    "classification": "internal",
                    "text": "SecureGraph is a hybrid RAG system.",
                },
            )
        ],
        None,
    )

    mock_session = AsyncMock()
    doc_result = MagicMock()
    doc_result.all.return_value = [MagicMock(document_id="doc_1", source_uri="file://paper.md")]
    trace_result = MagicMock()
    trace_result.all.return_value = [("chunk_1", "trace_99", None)]
    mock_session.execute.side_effect = [doc_result, trace_result]

    atlas = await build_vector_atlas(
        mock_client,
        mock_session,
        tenant_id="default",
        dims=2,
        limit=10,
    )

    assert atlas["total"] == 1
    assert atlas["projection_method"] == "pca"
    point = atlas["points"][0]
    assert point["chunk_id"] == "chunk_1"
    assert point["source_uri"] == "file://paper.md"
    assert point["last_trace_id"] == "trace_99"
    assert "x" in point and "y" in point
