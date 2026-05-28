from __future__ import annotations

import pytest

from securgraph.domain.community import Community, CommunitySummaryResponse

pytestmark = pytest.mark.unit


def test_community_carries_policy_class_key() -> None:
    c = Community(
        community_id="comm_l0_0_abc_t1",
        tenant_id="t1",
        title="T",
        summary="S",
        level=0,
        member_entities=("A", "B"),
        policy_class_key="abc1234567890def",
    )
    assert c.policy_class_key == "abc1234567890def"


def test_summary_response_carries_policy_class_key() -> None:
    r = CommunitySummaryResponse(
        community_id="comm_l0_0_abc_t1",
        title="T",
        summary="S",
        level=0,
        member_entities=["A", "B"],
        policy_class_key="abc1234567890def",
    )
    assert r.policy_class_key == "abc1234567890def"
