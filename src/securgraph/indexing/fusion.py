"""Reciprocal Rank Fusion (RRF) for hybrid retrieval result merging.

RRF combines ranked lists from multiple retrieval strategies without requiring
score normalisation or calibration across sources.

    RRF_Score(d) = sum_m ( 1 / (k + rank_m(d)) )

Reference: Cormack, Clarke, Buettcher (2009).
"""

from __future__ import annotations

from collections import defaultdict

from securgraph.domain.retrieval import RetrievalEvidence


def fuse_rankings(
    dense_results: list[RetrievalEvidence],
    lexical_results: list[RetrievalEvidence],
    *,
    k: int = 60,
    limit: int = 5,
) -> list[RetrievalEvidence]:
    """Merge dense and lexical evidence lists using Reciprocal Rank Fusion.

    Args:
        dense_results:   Evidence ranked by dense semantic vector search.
        lexical_results: Evidence ranked by full-text keyword search.
        k:               RRF smoothing constant (default 60, per original paper).
        limit:           Maximum number of results to return.

    Returns:
        Merged evidence list sorted by fused score descending, capped at
        ``limit`` items.  Each returned item has ``retrieval_strategy`` set to
        ``"rrf_fused"`` and ``included_in_prompt=True``.

    Notes:
        - If one list is empty the other is returned (up to ``limit`` items)
          with their original strategy preserved.
        - Duplicate ``object_id`` values are deduplicated; the dense evidence
          record wins when both lists contain the same object.
    """
    if not dense_results and not lexical_results:
        return []

    rrf_scores: dict[str, float] = defaultdict(float)
    evidence_map: dict[str, RetrievalEvidence] = {}

    # Score dense results (rank is 1-indexed)
    for rank, ev in enumerate(dense_results):
        key = ev.object_id
        rrf_scores[key] += 1.0 / (k + rank + 1)
        evidence_map[key] = ev  # dense takes priority on collision

    # Score lexical results
    for rank, ev in enumerate(lexical_results):
        key = ev.object_id
        rrf_scores[key] += 1.0 / (k + rank + 1)
        if key not in evidence_map:
            evidence_map[key] = ev

    # Sort by fused score and take top-limit
    sorted_keys = sorted(rrf_scores, key=lambda x: rrf_scores[x], reverse=True)[:limit]

    fused: list[RetrievalEvidence] = []
    for fused_rank, key in enumerate(sorted_keys):
        base = evidence_map[key]
        fused.append(
            base.model_copy(
                update={
                    "retrieval_strategy": "rrf_fused",
                    "original_rank": fused_rank,
                    "original_score": rrf_scores[key],
                    "included_in_prompt": True,
                }
            )
        )

    return fused
