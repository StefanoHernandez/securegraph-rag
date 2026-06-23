from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from securgraph.adapters.llm import get_llm_adapter_for, reset_llm_adapter_cache
from securgraph.config import get_settings

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clear_adapter_cache():
    reset_llm_adapter_cache()
    yield
    reset_llm_adapter_cache()


@pytest.mark.asyncio
async def test_verifier_ensemble_voting_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that enabling feature_verifier_ensemble runs 3 NLI calls and does majority voting."""
    settings = get_settings()
    settings.feature_verifier_ensemble = True

    adapter = get_llm_adapter_for("judge")

    # Sequential chat returns (raw JSON strings)
    mock_chat = AsyncMock(side_effect=[
        '{"label": "contradiction", "confidence": 0.9, "reason": "direct contradiction"}',
        '{"label": "contradiction", "confidence": 0.8, "reason": "strict contradiction"}',
        '{"label": "entailment", "confidence": 0.75, "reason": "some support"}'
    ])

    monkeypatch.setattr(adapter, "chat", mock_chat)

    result = await adapter.judge_nli("Cane e gatto", "Cane e uccello")

    assert result is not None
    # Winning vote should be contradiction since 2/3 voted contradiction
    assert result.label == "contradiction"
    # Average of winning confidence (0.9 + 0.8) / 2 = 0.85
    assert result.confidence == 0.85
    assert "direct contradiction" in result.reason
    assert "strict contradiction" in result.reason

    # Verify it called chat 3 times
    assert mock_chat.call_count == 3

    # Clean up
    settings.feature_verifier_ensemble = False


@pytest.mark.asyncio
async def test_verifier_ensemble_single_voter_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when feature_verifier_ensemble is disabled, it falls back to a single vote."""
    settings = get_settings()
    settings.feature_verifier_ensemble = False

    adapter = get_llm_adapter_for("judge")

    mock_chat = AsyncMock(
        return_value='{"label": "entailment", "confidence": 0.95, "reason": "clear support"}'
    )

    monkeypatch.setattr(adapter, "chat", mock_chat)

    result = await adapter.judge_nli("Cane e gatto", "Cane e gatto")

    assert result is not None
    assert result.label == "entailment"
    assert result.confidence == 0.95
    assert mock_chat.call_count == 1
