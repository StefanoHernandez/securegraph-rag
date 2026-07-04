from __future__ import annotations

import pytest

from securgraph.adapters.community_generator import _edges_to_class_edges
from securgraph.community.policy_class import (
    AccessLabel,
    partition_edges_by_class,
    policy_class_key,
)

pytestmark = pytest.mark.unit


def _label(owner: str) -> AccessLabel:
    return AccessLabel(owner=owner, group_viewers=frozenset(), direct_viewers=frozenset())


def test_partitioned_edges_record_their_single_source_policy_class() -> None:
    alice_key = policy_class_key(_label("alice"))
    bob_key = policy_class_key(_label("bob"))
    buckets = partition_edges_by_class(
        [
            {
                "source": "A",
                "target": "B",
                "type": "RELATES_TO",
                "confidence": 0.9,
                "source_chunk_ids": ["c1", "c2"],
            }
        ],
        {"c1": _label("alice"), "c2": _label("bob")},
    )

    assert buckets[alice_key][0].source_policy_class_keys == frozenset({alice_key})
    assert buckets[bob_key][0].source_policy_class_keys == frozenset({bob_key})


def test_ablation_edges_record_all_source_policy_classes() -> None:
    alice_key = policy_class_key(_label("alice"))
    bob_key = policy_class_key(_label("bob"))

    edges = _edges_to_class_edges(
        [
            {
                "source": "A",
                "target": "B",
                "type": "RELATES_TO",
                "confidence": 0.9,
                "source_chunk_ids": ["c1", "c2"],
            }
        ],
        {"c1": _label("alice"), "c2": _label("bob")},
    )

    assert edges[0].source_policy_class_keys == frozenset({alice_key, bob_key})
