from __future__ import annotations

import pytest

from securgraph.community.policy_class import (
    AccessLabel,
    partition_edges_by_class,
    policy_class_key,
)

pytestmark = pytest.mark.unit


def test_key_is_deterministic() -> None:
    a = AccessLabel(owner="alice", group_viewers=frozenset({"eng"}), direct_viewers=frozenset())
    b = AccessLabel(owner="alice", group_viewers=frozenset({"eng"}), direct_viewers=frozenset())
    assert policy_class_key(a) == policy_class_key(b)


def test_key_is_order_independent_for_groups() -> None:
    a = AccessLabel(
        owner="alice", group_viewers=frozenset({"eng", "sec"}), direct_viewers=frozenset()
    )
    b = AccessLabel(
        owner="alice", group_viewers=frozenset({"sec", "eng"}), direct_viewers=frozenset()
    )
    assert policy_class_key(a) == policy_class_key(b)


def test_key_changes_with_owner() -> None:
    a = AccessLabel(owner="alice", group_viewers=frozenset({"eng"}), direct_viewers=frozenset())
    b = AccessLabel(owner="bob", group_viewers=frozenset({"eng"}), direct_viewers=frozenset())
    assert policy_class_key(a) != policy_class_key(b)


def test_key_changes_with_direct_viewers() -> None:
    a = AccessLabel(
        owner="alice", group_viewers=frozenset({"eng"}), direct_viewers=frozenset({"carol"})
    )
    b = AccessLabel(owner="alice", group_viewers=frozenset({"eng"}), direct_viewers=frozenset())
    assert policy_class_key(a) != policy_class_key(b)


def test_key_is_128_bit_hex() -> None:
    a = AccessLabel(owner="alice", group_viewers=frozenset(), direct_viewers=frozenset())
    key = policy_class_key(a)
    assert len(key) == 32
    assert all(c in "0123456789abcdef" for c in key)


def _label(owner: str) -> AccessLabel:
    return AccessLabel(owner=owner, group_viewers=frozenset(), direct_viewers=frozenset())


def test_partition_groups_single_class_edges() -> None:
    chunk_labels = {"c1": _label("alice"), "c2": _label("alice")}
    edges = [
        {"source": "A", "target": "B", "type": "R", "confidence": 0.9, "source_chunk_ids": ["c1"]},
        {"source": "B", "target": "C", "type": "R", "confidence": 0.8, "source_chunk_ids": ["c2"]},
    ]
    buckets = partition_edges_by_class(edges, chunk_labels)
    assert len(buckets) == 1
    ((_key, class_edges),) = buckets.items()
    assert {e.source for e in class_edges} | {e.target for e in class_edges} == {"A", "B", "C"}


def test_partition_separates_distinct_classes() -> None:
    chunk_labels = {"c1": _label("alice"), "c2": _label("bob")}
    edges = [
        {"source": "A", "target": "B", "type": "R", "confidence": 0.9, "source_chunk_ids": ["c1"]},
        {"source": "C", "target": "D", "type": "R", "confidence": 0.8, "source_chunk_ids": ["c2"]},
    ]
    buckets = partition_edges_by_class(edges, chunk_labels)
    assert len(buckets) == 2


def test_multi_class_edge_fans_into_each_class() -> None:
    chunk_labels = {"c1": _label("alice"), "c2": _label("bob")}
    edges = [
        {
            "source": "A",
            "target": "B",
            "type": "R",
            "confidence": 0.9,
            "source_chunk_ids": ["c1", "c2"],
        },
    ]
    buckets = partition_edges_by_class(edges, chunk_labels)
    assert len(buckets) == 2
    for class_edges in buckets.values():
        assert len(class_edges) == 1


def test_edge_with_unknown_chunk_goes_to_unlabeled_class() -> None:
    chunk_labels: dict[str, AccessLabel] = {}
    edges = [
        {
            "source": "A",
            "target": "B",
            "type": "R",
            "confidence": 0.9,
            "source_chunk_ids": ["missing"],
        },
    ]
    buckets = partition_edges_by_class(edges, chunk_labels)
    assert "unlabeled" in buckets


def test_mixed_source_edge_appears_in_real_class_and_unlabeled() -> None:
    """An edge evidenced by both a labeled and an unknown chunk belongs to the
    real class (a user authorized there legitimately sees it via the labeled
    chunk) AND to the unlabeled class — not to one in place of the other."""
    chunk_labels = {"c1": _label("alice")}  # "c2" intentionally unknown
    edges = [
        {
            "source": "A",
            "target": "B",
            "type": "R",
            "confidence": 0.9,
            "source_chunk_ids": ["c1", "c2"],
        },
    ]
    buckets = partition_edges_by_class(edges, chunk_labels)
    real_key = policy_class_key(_label("alice"))
    assert set(buckets.keys()) == {real_key, "unlabeled"}
    assert len(buckets[real_key]) == 1
    assert len(buckets["unlabeled"]) == 1
