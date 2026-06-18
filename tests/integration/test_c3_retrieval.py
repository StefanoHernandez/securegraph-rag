from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.domain import Subject
from securgraph.domain.policy import OpenFGAPolicyAdapter
from securgraph.indexing.global_retriever import AuthorizedCommunityRetriever

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_user_authorized_only_to_one_class() -> None:
    """User authorized to class A receives A communities, never B."""
    key_a, key_b = "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"
    records = [
        {"community_id": f"comm_l1_0_{key_a}_t1", "title": "A", "summary": "sa",
         "level": 1, "member_entities": ["SSL"], "policy_class_key": key_a},
        {"community_id": f"comm_l1_0_{key_b}_t1", "title": "B", "summary": "sb",
         "level": 1, "member_entities": ["Payroll"], "policy_class_key": key_b},
    ]
    mock_run = AsyncMock()
    mock_result = MagicMock()
    mock_result.data = AsyncMock(return_value=records)
    mock_run.return_value = mock_result

    class MockSession:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self
        async def __aexit__(self, *a):  # type: ignore[no-untyped-def]
            pass
        async def run(self, cypher: str, **kwargs):  # type: ignore[no-untyped-def]
            return await mock_run(cypher, **kwargs)

    driver = MagicMock()
    driver.session = lambda: MockSession()

    # FGA allows only objects ending in key_a (both policy_class:key_a and
    # community:comm_l1_0_key_a_t1 — defense-in-depth check passes too).
    async def fake_check(req: Any) -> Any:
        obj = req.object if hasattr(req, "object") else req.tuple_key.object
        resp = MagicMock()
        resp.allowed = key_a in obj
        return resp

    fga_client = AsyncMock()
    fga_client.check.side_effect = fake_check
    retriever = AuthorizedCommunityRetriever(driver=driver, fga=OpenFGAPolicyAdapter(fga_client))

    subject = Subject(tenant_id="t1", user_id="user:alice")
    res = await retriever.retrieve_communities(subject=subject, level=1)

    keys = {r.policy_class_key for r in res}
    assert keys == {key_a}
