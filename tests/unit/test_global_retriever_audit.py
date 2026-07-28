"""Community retrieval audit coverage for allowed and denied candidates."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.domain import Subject
from securgraph.domain.policy import OpenFGAPolicyAdapter, PolicyDecisionValue
from securgraph.indexing.global_retriever import (
    AuthorizedCommunityRetriever,
    CommunityRetrievalResult,
)

pytestmark = pytest.mark.unit


async def test_community_retriever_returns_auditable_denied_candidates() -> None:
    records = [
        {
            "community_id": "comm-allowed",
            "title": "Allowed",
            "summary": "Allowed summary",
            "level": 1,
            "member_entities": ["A"],
            "policy_class_key": "allowed-class",
        },
        {
            "community_id": "comm-denied",
            "title": "Denied",
            "summary": "Denied summary",
            "level": 1,
            "member_entities": ["B"],
            "policy_class_key": "denied-class",
        },
    ]
    neo_result = MagicMock()
    neo_result.data = AsyncMock(return_value=records)
    neo_session = AsyncMock()
    neo_session.run = AsyncMock(return_value=neo_result)
    neo_session.__aenter__.return_value = neo_session
    driver = MagicMock()
    driver.session.return_value = neo_session

    async def check(request: Any) -> MagicMock:
        response = MagicMock()
        object_id = request.object
        response.allowed = "allowed" in object_id
        return response

    fga_client = AsyncMock()
    fga_client.check.side_effect = check
    retriever = AuthorizedCommunityRetriever(driver, OpenFGAPolicyAdapter(fga_client))

    result = await retriever.retrieve_communities(
        subject=Subject(tenant_id="mrd", user_id="user:alice"),
        trace_id="trace-community",
        return_decisions=True,
    )

    assert isinstance(result, CommunityRetrievalResult)
    assert [community.community_id for community in result.communities] == ["comm-allowed"]
    by_id = {decision.object_id: decision for decision in result.decisions}
    assert by_id["comm-allowed"].decision is PolicyDecisionValue.ALLOW
    assert by_id["comm-allowed"].retriever_id == "community"
    assert result.communities[0].policy_check_id == by_id["comm-allowed"].decision_id
    assert by_id["comm-denied"].decision is PolicyDecisionValue.DENY
    assert by_id["comm-denied"].reason_code == "community_policy_class_denied"
    assert by_id["comm-denied"].resource_hash.startswith("sha256:")
