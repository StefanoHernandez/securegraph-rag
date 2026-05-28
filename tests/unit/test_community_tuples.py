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
