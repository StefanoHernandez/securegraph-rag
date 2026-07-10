"""2D projection helpers for Vector Atlas.

Dense vectors never leave the server. Small tenants use PCA on scrolled vectors;
larger tenants use Qdrant distance-matrix pairs + classical MDS so embeddings stay
in Qdrant.
"""

from __future__ import annotations

import numpy as np

MATRIX_PROJECTION_THRESHOLD = 100


def project_pca(vectors: list[list[float]], *, dims: int = 2) -> list[list[float]]:
    """Project high-dimensional vectors to ``dims`` via centered PCA (SVD)."""
    if not vectors:
        return []
    if len(vectors) == 1:
        return [[0.0] * dims]

    matrix = np.asarray(vectors, dtype=np.float64)
    matrix = matrix - matrix.mean(axis=0)
    _, singular, vt = np.linalg.svd(matrix, full_matrices=False)
    rank = min(dims, vt.shape[0], len(singular))
    if rank == 0:
        return [[0.0] * dims for _ in vectors]

    coords = matrix @ vt[:rank].T
    if coords.shape[1] < dims:
        pad = np.zeros((coords.shape[0], dims - coords.shape[1]))
        coords = np.hstack([coords, pad])
    result: list[list[float]] = coords[:, :dims].tolist()
    return result


def _similarity_to_distance(score: float) -> float:
    """Convert Qdrant cosine similarity to a distance in [0, 2]."""
    return float(max(0.0, min(2.0, 1.0 - score)))


def project_mds_from_pairs(
    point_ids: list[str],
    pairs: list[tuple[str, str, float]],
    *,
    dims: int = 2,
) -> dict[str, list[float]]:
    """Classical MDS on a sparse cosine-similarity graph from Qdrant matrix API."""
    if not point_ids:
        return {}
    if len(point_ids) == 1:
        return {point_ids[0]: [0.0] * dims}

    index = {pid: idx for idx, pid in enumerate(point_ids)}
    n = len(point_ids)
    dist = np.full((n, n), 2.0, dtype=np.float64)
    np.fill_diagonal(dist, 0.0)

    for left, right, score in pairs:
        i = index.get(left)
        j = index.get(right)
        if i is None or j is None:
            continue
        d = _similarity_to_distance(score)
        dist[i, j] = min(dist[i, j], d)
        dist[j, i] = min(dist[j, i], d)

    dist_sq = dist**2
    centering = np.eye(n) - np.ones((n, n)) / n
    gram = -0.5 * centering @ dist_sq @ centering
    eigvals, eigvecs = np.linalg.eigh(gram)
    order = np.argsort(eigvals)[::-1]
    eigvals = np.maximum(eigvals[order[:dims]], 0.0)
    eigvecs = eigvecs[:, order[:dims]]
    coords = eigvecs * np.sqrt(eigvals)

    if coords.shape[1] < dims:
        pad = np.zeros((coords.shape[0], dims - coords.shape[1]))
        coords = np.hstack([coords, pad])

    return {point_ids[i]: coords[i, :dims].tolist() for i in range(n)}
