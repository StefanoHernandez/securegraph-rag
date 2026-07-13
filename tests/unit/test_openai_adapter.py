"""Unit tests for the cloud OpenAI-compatible adapter used by OpenAI and Mistral."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import Request, Response
from openai import APIStatusError

from securgraph.adapters.llm.base import NLIResult
from securgraph.adapters.llm.openai import OpenAILLMAdapter
from securgraph.config import Settings

pytestmark = pytest.mark.unit


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> tuple[OpenAILLMAdapter, MagicMock]:
    client = MagicMock()
    client.chat.completions.create = AsyncMock()
    monkeypatch.setattr(
        "securgraph.adapters.llm.openai._get_openai_client",
        lambda _base_url, _api_key: client,
    )
    settings = Settings(_env_file=None, OPENAI_API_KEY="test-openai-key")
    return OpenAILLMAdapter(role="generation", settings=settings), client


async def test_chat_sends_json_contract_and_returns_completion(
    adapter: tuple[OpenAILLMAdapter, MagicMock],
) -> None:
    cloud_adapter, client = adapter
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="cloud answer"))]
    )

    response = await cloud_adapter.chat(
        system="system instructions",
        user="user question",
        json_mode=True,
        temperature=0.25,
    )

    assert response == "cloud answer"
    client.chat.completions.create.assert_awaited_once_with(
        model="mistral-small-latest",
        messages=[
            {"role": "system", "content": "system instructions"},
            {"role": "user", "content": "user question"},
        ],
        temperature=0.25,
        response_format={"type": "json_object"},
    )


async def test_chat_retries_a_rate_limited_openai_compatible_request(
    adapter: tuple[OpenAILLMAdapter, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud_adapter, client = adapter
    rate_limited = APIStatusError(
        "rate limited",
        response=Response(
            429,
            headers={"retry-after": "0"},
            request=Request("POST", "https://api.example/v1/chat/completions"),
        ),
        body=None,
    )
    client.chat.completions.create.side_effect = [
        rate_limited,
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="recovered"))]),
    ]
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)

    response = await cloud_adapter.chat(system="system", user="user")

    assert response == "recovered"
    assert client.chat.completions.create.await_count == 2
    sleep.assert_awaited_once_with(0.0)


async def test_extract_graph_slice_normalizes_valid_items_and_defaults_confidence(
    adapter: tuple[OpenAILLMAdapter, MagicMock],
) -> None:
    cloud_adapter, _ = adapter
    cloud_adapter.chat = AsyncMock(
        return_value=json.dumps(
            {
                "entities": [
                    {"name": "SecureGraph", "label": "Project"},
                    {"name": "   ", "label": "Invalid"},
                    "not-an-entity",
                ],
                "relations": [
                    {
                        "source_entity_name": "SecureGraph",
                        "target_entity_name": "MinIO",
                        "relationship_type": "ANCHORS_TO",
                    },
                    {"source_entity_name": "missing required fields"},
                ],
            }
        )
    )

    graph = await cloud_adapter.extract_graph_slice("SecureGraph anchors to MinIO.")

    assert [(entity.name, entity.label) for entity in graph.entities] == [
        ("SecureGraph", "Project")
    ]
    assert len(graph.relations) == 1
    assert graph.relations[0].confidence == 0.5


async def test_extract_claims_normalizes_entity_variants_and_discards_empty_claims(
    adapter: tuple[OpenAILLMAdapter, MagicMock],
) -> None:
    cloud_adapter, _ = adapter
    cloud_adapter.chat = AsyncMock(
        return_value=json.dumps(
            {
                "claims": [
                    {
                        "id": "claim-1",
                        "claim": "MinIO retains anchors for 365 days.",
                        "entities": ["MinIO", {"entity": "anchor"}, {"name": "365 days"}],
                    },
                    {"claim_id": "empty", "text": ""},
                    "not-a-claim",
                ]
            }
        )
    )

    claims = await cloud_adapter.extract_claims("Describe the anchor policy.")

    assert len(claims.claims) == 1
    assert claims.claims[0].claim_id == "claim-1"
    assert claims.claims[0].entities == ["MinIO", "anchor", "365 days"]


async def test_single_nli_vote_normalizes_boolean_label_and_confidence(
    adapter: tuple[OpenAILLMAdapter, MagicMock],
) -> None:
    cloud_adapter, _ = adapter
    cloud_adapter.chat = AsyncMock(
        return_value='{"entailment": "yes", "confidence": "0.82", "reason": "supported"}'
    )

    vote = await cloud_adapter._single_nli_vote("system", "claim and context", 0.0)

    assert vote == NLIResult(label="entailment", confidence=0.82, reason="supported")


async def test_single_nli_vote_fails_closed_on_cloud_error(
    adapter: tuple[OpenAILLMAdapter, MagicMock],
) -> None:
    cloud_adapter, _ = adapter
    cloud_adapter.chat = AsyncMock(side_effect=RuntimeError("provider unavailable"))

    vote = await cloud_adapter._single_nli_vote("system", "claim and context", 0.0)

    assert vote.label == "neutral"
    assert vote.confidence == 0.0
    assert "provider unavailable" in vote.reason


async def test_ensemble_uses_majority_label_and_average_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    monkeypatch.setattr(
        "securgraph.adapters.llm.openai._get_openai_client",
        lambda _base_url, _api_key: client,
    )
    settings = Settings(
        _env_file=None,
        OPENAI_API_KEY="test-openai-key",
        feature_verifier_ensemble=True,
    )
    cloud_adapter = OpenAILLMAdapter(role="judge", settings=settings)
    cloud_adapter._single_nli_vote = AsyncMock(
        side_effect=[
            NLIResult(label="entailment", confidence=0.8, reason="first"),
            NLIResult(label="contradiction", confidence=0.9, reason="second"),
            NLIResult(label="contradiction", confidence=0.7, reason="third"),
        ]
    )

    result = await cloud_adapter.judge_nli("The anchor is immutable.", "MinIO Object Lock.")

    assert result == NLIResult(
        label="contradiction",
        confidence=0.8,
        reason="Voter: second; Voter: third",
    )
