from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.config import get_settings
from securgraph.domain import Subject
from securgraph.domain.consensus import Claim, VerifierStatus
from securgraph.ingestion.self_healing import resolve_graph_contradiction
from securgraph.observability.otel import GRAPH_SELF_HEALING_COUNT
from securgraph.orchestration.workflow import QueryState, compile_workflow

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_self_healing_pipeline_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """A contradiction triggers graph resolution and repairs Neo4j."""
    settings = get_settings()
    settings.feature_self_healing_enabled = True

    # 1. Mock Neo4j driver and transaction recording
    mock_run = AsyncMock()

    class MockTx:
        async def run(self, cypher: str, **kwargs) -> Any:
            return await mock_run(cypher, **kwargs)

    class MockSession:
        async def __aenter__(self) -> MockSession:
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
            pass

        async def execute_write(self, transaction_fn, *args, **kwargs) -> None:
            await transaction_fn(MockTx(), *args, **kwargs)

    mock_driver = MagicMock()
    mock_driver.session = lambda: MockSession()

    # 2. Mock LLM adapter for self-healing JSON response
    mock_adapter = AsyncMock()
    mock_adapter.chat.return_value = (
        "{\n"
        '  "source_entity": "Cane",\n'
        '  "incorrect_target_entity": "Rosso",\n'
        '  "corrected_target_entity": "Blu",\n'
        '  "action": "update",\n'
        '  "reason": "Il testo originale dichiara esplicitamente che il cane è blu."\n'
        "}"
    )

    monkeypatch.setattr(
        "securgraph.ingestion.self_healing.get_llm_adapter_for", lambda role: mock_adapter
    )

    # Reset metrics count before testing
    try:
        initial_metric_val = GRAPH_SELF_HEALING_COUNT.labels(
            tenant_id="test_tenant_self_healing", action="update"
        )._value.get()
    except Exception:
        initial_metric_val = 0.0

    # 3. Test resolve_graph_contradiction directly
    res = await resolve_graph_contradiction(
        driver=mock_driver,
        tenant_id="test_tenant_self_healing",
        claim_text="Cane è rosso",
        vector_context="Il cane è blu ed il gatto è nero.",
    )

    assert res is not None
    assert res["source_entity"] == "Cane"
    assert res["incorrect_target_entity"] == "Rosso"
    assert res["corrected_target_entity"] == "Blu"
    assert res["action"] == "update"

    # Assert that Neo4j Cypher write queries were run
    assert mock_run.call_count >= 1
    # Check that it deleted the old relation and merged the new one
    delete_call = mock_run.call_args_list[0]
    assert "DELETE r" in delete_call[0][0]
    assert delete_call[1]["source_name"] == "Cane"
    assert delete_call[1]["target_name"] == "Rosso"

    update_call = mock_run.call_args_list[1]
    assert "MERGE (tgt:Entity" in update_call[0][0]
    assert update_call[1]["source_name"] == "Cane"
    assert update_call[1]["new_target_name"] == "Blu"

    # Assert metric increment
    final_metric_val = GRAPH_SELF_HEALING_COUNT.labels(
        tenant_id="test_tenant_self_healing", action="update"
    )._value.get()
    assert final_metric_val == initial_metric_val + 1.0

    # Reset settings
    settings.feature_self_healing_enabled = False


@pytest.mark.asyncio
async def test_workflow_self_healing_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    """The consensus verifier schedules graph resolution on contradiction."""
    settings = get_settings()
    settings.feature_self_healing_enabled = True

    # Mock resolve_graph_contradiction to track if it is called
    mock_resolve = AsyncMock()
    monkeypatch.setattr(
        "securgraph.ingestion.self_healing.resolve_graph_contradiction", mock_resolve
    )

    # Mock Ollama NLI judge and other workflow requirements
    mock_nli_entailment = MagicMock()
    mock_nli_entailment.label = "entailment"
    mock_nli_entailment.confidence = 0.9

    mock_nli_contradiction = MagicMock()
    mock_nli_contradiction.label = "contradiction"
    mock_nli_contradiction.confidence = 0.95

    # Mock NLI judge return values sequentially
    # First: vector claim NLI check (returns entailment)
    # Second: graph claim NLI check (returns contradiction)
    mock_adapter = MagicMock()
    mock_adapter.judge_nli = AsyncMock()
    mock_adapter.judge_nli.side_effect = [mock_nli_entailment, mock_nli_contradiction]
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for", lambda role: mock_adapter
    )

    # 1. Construct State Graph inputs
    qdrant = MagicMock()
    graph = MagicMock()
    graph.driver = MagicMock()  # Mock Neo4j driver attribute

    workflow = compile_workflow(qdrant, graph)

    state_input: QueryState = {
        "query": "Qual è il colore del cane?",
        "subject": Subject(tenant_id="test_tenant_sh_workflow", user_id="user:stefano"),
        "options": {"consensus_threshold": 0.7},
        "trace_id": "test_trace_sh_wf",
        "vector_context": [],
        "vector_draft": "Il cane è blu.",
        "vector_claims": [
            Claim(
                claim_id="vc0",
                text="Il cane è blu",
                supporting_evidence_ids=(),
                verifier_status=VerifierStatus.NOT_CHECKED,
            )
        ],
        "graph_context": [],
        "graph_draft": "Cane è rosso",
        "graph_claims": [
            Claim(
                claim_id="gc0",
                text="Cane è rosso",
                supporting_evidence_ids=(),
                verifier_status=VerifierStatus.NOT_CHECKED,
            )
        ],
        "consensus_report": None,
        "final_answer": None,
        "warnings": [],
    }

    # Retrieve only the consensus_verifier node from the StateGraph to execute it in isolation
    node_obj = workflow.builder.nodes["consensus_verifier"]
    result = await node_obj.runnable.ainvoke(state_input)
    assert result is not None

    # Wait a tiny moment for background task asyncio.create_task to invoke the mocked resolver
    await asyncio.sleep(0.1)

    # Assert that self-healing was indeed triggered due to the contradiction label on graph claim
    assert mock_resolve.call_count == 1
    call_args = mock_resolve.call_args[1]
    assert call_args["tenant_id"] == "test_tenant_sh_workflow"
    assert call_args["claim_text"] == "Cane è rosso"
    assert call_args["vector_context"] == "Il cane è blu."

    # Clean up
    settings.feature_self_healing_enabled = False
