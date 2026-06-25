"""Hypothesis property test: the C3 no-leakage guarantee.

Headline contribution C3 promises that no community — and no community summary —
can span more than one policy-equivalence-class. This is enforced structurally
by `partition_edges_by_class`: each per-class bucket only ever contains edges
that have at least one source chunk in that class. Community detection runs
INSIDE one bucket, so a community's members are derived only from edges of a
single class. This property test pins that structural guarantee against random
graphs and random per-chunk access labels.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from securgraph.community.policy_class import (
    AccessLabel,
    partition_edges_by_class,
    policy_class_key,
)

pytestmark = pytest.mark.adversarial

owners = st.sampled_from(["alice", "bob", "carol", "dave"])
names = st.text(alphabet="ABCDEFGH", min_size=1, max_size=2)


@st.composite
def graph_with_labels(draw):  # type: ignore[no-untyped-def]
    chunk_ids = [f"c{i}" for i in range(draw(st.integers(min_value=1, max_value=5)))]
    chunk_labels = {
        cid: AccessLabel(
            owner=draw(owners),
            group_viewers=frozenset(),
            direct_viewers=frozenset(),
        )
        for cid in chunk_ids
    }
    edges = draw(
        st.lists(
            st.fixed_dictionaries(
                {
                    "source": names,
                    "target": names,
                    "type": st.just("R"),
                    "confidence": st.floats(min_value=0.0, max_value=1.0),
                    "source_chunk_ids": st.lists(
                        st.sampled_from(chunk_ids), min_size=1, max_size=2
                    ),
                }
            ),
            min_size=1,
            max_size=10,
        )
    )
    return chunk_labels, edges


@settings(max_examples=200)
@given(graph_with_labels())
def test_no_bucket_contains_a_cross_class_edge(data) -> None:  # type: ignore[no-untyped-def]
    """Every edge in a class bucket must trace back to a source chunk of that class.

    This is the structural guarantee: community detection runs within a single
    bucket, so no community/summary can ever span access classes.
    """
    chunk_labels, edges = data
    buckets = partition_edges_by_class(edges, chunk_labels)

    for class_key, class_edges in buckets.items():
        assert class_edges, "no empty buckets"
        for ce in class_edges:
            # find original edges matching this projected edge
            matching = [
                e
                for e in edges
                if e["source"] == ce.source and e["target"] == ce.target and e["type"] == ce.type
            ]
            assert matching, "projected edge must trace to an original edge"
            legit_keys: set[str] = set()
            for e in matching:
                for cid in e["source_chunk_ids"]:
                    label = chunk_labels.get(cid)
                    legit_keys.add(policy_class_key(label) if label else "unlabeled")
            assert class_key in legit_keys, "edge placed in a class it does not belong to"
