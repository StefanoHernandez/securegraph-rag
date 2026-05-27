from __future__ import annotations

import pytest

from securgraph.community.policy_class import AccessLabel, policy_class_key

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


def test_key_is_short_hex() -> None:
    a = AccessLabel(owner="alice", group_viewers=frozenset(), direct_viewers=frozenset())
    key = policy_class_key(a)
    assert len(key) == 16
    assert all(c in "0123456789abcdef" for c in key)
