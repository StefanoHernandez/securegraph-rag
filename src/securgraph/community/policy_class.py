"""Policy-equivalence-class core for C3 — pure, no I/O.

A policy_class_key is a deterministic, order-independent fingerprint of a source
chunk's authorization label. Under the ReBAC model (viewer = owner OR group_viewer
OR direct_viewer), identical labels imply an identical authorized-subject set by
construction, so grouping by this key partitions the graph into access classes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


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
