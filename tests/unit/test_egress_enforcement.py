"""Egress policy enforcement at the LLM adapter dispatch boundary."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from securgraph.adapters.llm import get_llm_adapter, reset_llm_adapter_cache
from securgraph.policy.egress import (
    EgressBlockedError,
    decide_egress,
    enforce_egress,
)
from securgraph.runtime_overrides import clear_runtime_overrides, set_runtime_override

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_state():
    clear_runtime_overrides()
    reset_llm_adapter_cache()
    yield
    clear_runtime_overrides()
    reset_llm_adapter_cache()


# ---------------------------------------------------------------------------
# decide_egress / enforce_egress pure logic
# ---------------------------------------------------------------------------


def test_no_policy_configured_allows() -> None:
    # No override stored for the tenant -> allowed, no exception.
    enforce_egress("tenant-x", "openai", "generation")


def test_block_rule_matches_provider_and_stage() -> None:
    set_runtime_override(
        "acme",
        "egress_policy",
        {
            "default": "allow",
            "rules": [{"provider": "openai", "stage": "judge", "action": "block"}],
        },
    )
    # Non-matching stage passes...
    enforce_egress("acme", "openai", "generation")
    # ...matching provider+stage blocks.
    with pytest.raises(EgressBlockedError, match="provider=openai stage=judge"):
        enforce_egress("acme", "openai", "judge")


def test_any_wildcard_matches_all_providers() -> None:
    set_runtime_override(
        "acme",
        "egress_policy",
        {"default": "allow", "rules": [{"provider": "any", "stage": "any", "action": "block"}]},
    )
    with pytest.raises(EgressBlockedError):
        enforce_egress("acme", "mistral", "extract")


def test_default_block_policy_blocks_everything() -> None:
    set_runtime_override("acme", "egress_policy", {"default": "block", "rules": []})
    with pytest.raises(EgressBlockedError, match="default egress policy"):
        enforce_egress("acme", "ollama", "generation")


def test_classification_ranking_gates_rule_match() -> None:
    policy = {
        "default": "block",
        "rules": [
            {
                "provider": "openai",
                "stage": "any",
                "max_classification": "internal",
                "action": "allow",
            }
        ],
    }
    # internal <= internal -> rule matches -> allow
    assert (
        decide_egress(
            policy, provider="openai", stage="generation", classification="internal"
        ).allowed
        is True
    )
    # confidential > internal -> rule skipped -> default block
    assert (
        decide_egress(
            policy, provider="openai", stage="generation", classification="confidential"
        ).allowed
        is False
    )


def test_invalid_stored_policy_fails_closed() -> None:
    set_runtime_override("acme", "egress_policy", {"default": "bogus-action", "rules": []})
    with pytest.raises(EgressBlockedError, match="invalid stored egress policy"):
        enforce_egress("acme", "openai", "generation")


# ---------------------------------------------------------------------------
# Adapter dispatch integration
# ---------------------------------------------------------------------------


def test_get_llm_adapter_blocked_by_policy() -> None:
    set_runtime_override("acme", "egress_policy", {"default": "block", "rules": []})
    with pytest.raises(EgressBlockedError):
        get_llm_adapter("generation", tenant_id="acme")


def test_get_llm_adapter_unaffected_tenant_still_resolves() -> None:
    set_runtime_override("acme", "egress_policy", {"default": "block", "rules": []})
    # A different tenant without a policy must still get an adapter.
    adapter = get_llm_adapter("generation", tenant_id="other-tenant")
    assert adapter is not None


# ---------------------------------------------------------------------------
# Route surface: /query maps the block to HTTP 451
# ---------------------------------------------------------------------------


async def test_query_route_returns_451_when_egress_blocked(fastapi_app, client) -> None:
    from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user

    principal = AuthenticatedPrincipal(
        user_id="alice",
        username="alice",
        tenant_id="acme",
        role="analyst",
        is_super_admin=False,
        must_change_password=False,
        session_id="ses-1",
    )
    fastapi_app.dependency_overrides[require_user] = lambda: principal

    workflow = AsyncMock()
    workflow.ainvoke.side_effect = EgressBlockedError(
        "acme", "openai", "generation", "default egress policy"
    )
    with patch("securgraph.api.routes.query.compile_workflow", return_value=workflow):
        response = await client.post(
            "/query",
            json={
                "tenant_id": "acme",
                "user_id": "user:alice",
                "query": "anything",
                "options": {},
            },
        )

    assert response.status_code == 451
    assert "Egress blocked by tenant policy" in response.json()["detail"]
