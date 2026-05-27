from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
import pytest

from securgraph.adapters.ollama import judge_nli
from securgraph.config import get_settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_verifier_ensemble_voting_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that enabling feature_verifier_ensemble runs 3 NLI calls and does majority voting."""
    settings = get_settings()
    settings.feature_verifier_ensemble = True

    mock_client = AsyncMock()
    
    # Voter 1 returns contradiction
    mock_res_1 = MagicMock()
    mock_res_1.message.content = '{"label": "contradiction", "confidence": 0.9, "reason": "direct contradiction"}'
    
    # Voter 2 returns contradiction
    mock_res_2 = MagicMock()
    mock_res_2.message.content = '{"label": "contradiction", "confidence": 0.8, "reason": "strict contradiction"}'
    
    # Voter 3 returns entailment
    mock_res_3 = MagicMock()
    mock_res_3.message.content = '{"label": "entailment", "confidence": 0.75, "reason": "some support"}'

    # Sequential chat returns
    mock_client.chat.side_effect = [mock_res_1, mock_res_2, mock_res_3]

    monkeypatch.setattr(
        "securgraph.adapters.ollama.get_ollama_client",
        lambda: mock_client
    )

    result = await judge_nli("Cane e gatto", "Cane e uccello")
    
    assert result is not None
    # Winning vote should be contradiction since 2/3 voted contradiction
    assert result.label == "contradiction"
    # Average of winning confidence (0.9 + 0.8) / 2 = 0.85
    assert result.confidence == 0.85
    assert "direct contradiction" in result.reason
    assert "strict contradiction" in result.reason
    
    # Verify it called chat 3 times
    assert mock_client.chat.call_count == 3
    
    # Clean up
    settings.feature_verifier_ensemble = False


@pytest.mark.asyncio
async def test_verifier_ensemble_single_voter_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that when feature_verifier_ensemble is disabled, it falls back to a single vote."""
    settings = get_settings()
    settings.feature_verifier_ensemble = False

    mock_client = AsyncMock()
    mock_res = MagicMock()
    mock_res.message.content = '{"label": "entailment", "confidence": 0.95, "reason": "clear support"}'
    mock_client.chat.return_value = mock_res

    monkeypatch.setattr(
        "securgraph.adapters.ollama.get_ollama_client",
        lambda: mock_client
    )

    result = await judge_nli("Cane e gatto", "Cane e gatto")
    
    assert result is not None
    assert result.label == "entailment"
    assert result.confidence == 0.95
    assert mock_client.chat.call_count == 1
