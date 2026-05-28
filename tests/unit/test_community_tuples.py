from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.adapters.community_generator import emit_community_authorization_tuples
from securgraph.community.policy_class import AccessLabel, policy_class_key

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_emits_policy_class_and_community_tuples() -> None:
    label = AccessLabel(
        owner="alice",
        group_viewers=frozenset({"eng"}),
        direct_viewers=frozenset({"carol"}),
    )
    key = policy_class_key(label)
    chunk_labels = {"c1": label}
    buckets = {key: []}  # bucket contents irrelevant for tuple emission
    written = [{"community_id": f"comm_l0_0_{key}_t1", "policy_class_key": key}]

    fga = MagicMock()
    fga.client = MagicMock()
    fga.client.write = AsyncMock()

    await emit_community_authorization_tuples(fga, chunk_labels, buckets, written)

    fga.client.write.assert_awaited()
    req = fga.client.write.call_args[0][0]
    users = {(w.user, w.relation, w.object) for w in req.writes}
    assert ("user:alice", "owner", f"policy_class:{key}") in users
    assert ("group:eng#member", "group_viewer", f"policy_class:{key}") in users
    assert ("user:carol", "direct_viewer", f"policy_class:{key}") in users
    assert (f"policy_class:{key}", "in_class", f"community:comm_l0_0_{key}_t1") in users


@pytest.mark.asyncio
async def test_unlabeled_class_emits_no_grants_only_in_class() -> None:
    """A community whose class has no known label receives NO grant tuples.

    The policy_class object thus has no viewer relation satisfied, making the
    community invisible at retrieval (fail-closed). This pins the C3 guarantee
    for legacy/unlabeled data.
    """
    chunk_labels: dict = {}  # no labels known
    buckets = {"unlabeled": []}
    written = [{"community_id": "comm_l0_0_unlabeled_t1", "policy_class_key": "unlabeled"}]

    fga = MagicMock()
    fga.client = MagicMock()
    fga.client.write = AsyncMock()

    await emit_community_authorization_tuples(fga, chunk_labels, buckets, written)

    fga.client.write.assert_awaited_once()
    req = fga.client.write.call_args[0][0]
    relations = {w.relation for w in req.writes}
    # Only in_class allowed; no owner/group_viewer/direct_viewer
    assert relations == {"in_class"}
    assert len(req.writes) == 1
    assert req.writes[0].user == "policy_class:unlabeled"
    assert req.writes[0].object == "community:comm_l0_0_unlabeled_t1"
