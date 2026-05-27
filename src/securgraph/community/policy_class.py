"""Policy-equivalence-class core for C3 — pure, no I/O.

A policy_class_key is a deterministic, order-independent fingerprint of a source
chunk's authorization label. Under the ReBAC model (viewer = owner OR group_viewer
OR direct_viewer), identical labels imply an identical authorized-subject set by
construction, so grouping by this key partitions the graph into access classes.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AccessLabel:
    """The authorization determinant of a source chunk."""

    owner: str
    group_viewers: frozenset[str] = field(default_factory=frozenset)
    direct_viewers: frozenset[str] = field(default_factory=frozenset)


def policy_class_key(label: AccessLabel) -> str:
    """Return a deterministic 16-hex-char fingerprint of the access label."""
    canonical = json.dumps(
        {
            "owner": label.owner,
            "group_viewers": sorted(label.group_viewers),
            "direct_viewers": sorted(label.direct_viewers),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


UNLABELED_CLASS_KEY = "unlabeled"


@dataclass(frozen=True)
class ClassEdge:
    """An edge as it belongs to one specific policy class."""

    source: str
    target: str
    type: str
    confidence: float


def partition_edges_by_class(
    edges: list[dict[str, Any]],
    chunk_labels: dict[str, AccessLabel],
) -> dict[str, list[ClassEdge]]:
    """Group edges into per-class buckets (per-class projection).

    Each edge belongs to EVERY policy class among the labels of its source
    chunks. A mixed edge — sourced from both a labeled chunk and an unlabeled
    one — therefore appears in BOTH the real class bucket and the unlabeled
    bucket: it is legitimately part of the real class (a user authorized there
    sees it via the labeled chunk), while the unlabeled contribution is kept
    separate so it is never silently merged INTO a real class. A source chunk
    with no known label maps to ``UNLABELED_CLASS_KEY``; an edge with no source
    chunks at all also falls back to ``UNLABELED_CLASS_KEY``.
    """
    buckets: dict[str, list[ClassEdge]] = defaultdict(list)
    for edge in edges:
        keys: set[str] = set()
        for chunk_id in edge.get("source_chunk_ids") or []:
            label = chunk_labels.get(chunk_id)
            keys.add(policy_class_key(label) if label is not None else UNLABELED_CLASS_KEY)
        if not keys:
            keys.add(UNLABELED_CLASS_KEY)
        ce = ClassEdge(
            source=edge["source"],
            target=edge["target"],
            type=edge["type"],
            confidence=float(edge.get("confidence", 0.5)),
        )
        for key in keys:
            buckets[key].append(ce)
    return dict(buckets)
