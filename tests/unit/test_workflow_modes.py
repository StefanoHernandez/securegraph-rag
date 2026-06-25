from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.domain import RetrievalEvidence, RetrieverId, Subject
from securgraph.orchestration.workflow import compile_workflow

pytestmark = pytest.mark.unit


def _empty_state(mode: str) -> dict[str, Any]:
    return {
        "query": "Who is Stefano?",
        "subject": Subject(tenant_id="t1", user_id="user:alice"),
        "options": {"retrieval_mode": mode, "top_k": 5, "consensus_threshold": 0.7},
        "trace_id": "tr1",
        "vector_context": [],
        "vector_draft": None,
        "vector_claims": [],
        "graph_context": [],
        "graph_draft": None,
        "graph_claims": [],
        "consensus_report": None,
        "final_answer": None,
        "warnings": [],
    }


def _build_mocks() -> tuple[Any, Any]:
    qdrant = MagicMock()
    qdrant.search_dense = AsyncMock(return_value=[])
    qdrant.search_lexical = AsyncMock(return_value=[])
    graph = MagicMock()
    graph.traverse_authorized_subgraph = AsyncMock(return_value=[])
    graph.driver = MagicMock()
    graph.fga = MagicMock()
    return qdrant, graph


def _fake_adapter(text: str = "draft") -> Any:
    """Build a fake LLMAdapter whose chat() returns ``text`` and whose
    extract_claims / judge_nli return empty results. Used so the workflow
    exercises its LLM call sites without hitting any real provider."""
    adapter = MagicMock()
    adapter.chat = AsyncMock(return_value=text)
    adapter.extract_claims = AsyncMock(return_value=MagicMock(claims=[]))
    adapter.judge_nli = AsyncMock(
        return_value=MagicMock(label="neutral", confidence=0.5, reason="mock")
    )
    return adapter


@pytest.mark.asyncio
async def test_vector_mode_skips_graph_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    qdrant, graph = _build_mocks()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("vector answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("vector answer"),
    )

    workflow = compile_workflow(qdrant, graph)
    result = await workflow.ainvoke(_empty_state("vector"))

    qdrant.search_dense.assert_awaited()
    qdrant.search_lexical.assert_awaited()
    graph.traverse_authorized_subgraph.assert_not_awaited()
    assert result["graph_context"] == []
    assert (result["graph_draft"] or "") == ""


@pytest.mark.asyncio
async def test_lexical_mode_skips_dense_and_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    qdrant, graph = _build_mocks()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("lexical answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("lexical answer"),
    )

    workflow = compile_workflow(qdrant, graph)
    await workflow.ainvoke(_empty_state("lexical"))

    qdrant.search_dense.assert_not_awaited()
    qdrant.search_lexical.assert_awaited()
    graph.traverse_authorized_subgraph.assert_not_awaited()


@pytest.mark.asyncio
async def test_graph_mode_skips_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    qdrant, graph = _build_mocks()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("graph answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("graph answer"),
    )

    workflow = compile_workflow(qdrant, graph)
    await workflow.ainvoke(_empty_state("graph"))

    qdrant.search_dense.assert_not_awaited()
    qdrant.search_lexical.assert_not_awaited()
    graph.traverse_authorized_subgraph.assert_awaited()


@pytest.mark.asyncio
async def test_hybrid_mode_runs_both(monkeypatch: pytest.MonkeyPatch) -> None:
    qdrant, graph = _build_mocks()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("hybrid answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("hybrid answer"),
    )

    workflow = compile_workflow(qdrant, graph)
    await workflow.ainvoke(_empty_state("hybrid"))

    qdrant.search_dense.assert_awaited()
    qdrant.search_lexical.assert_awaited()
    graph.traverse_authorized_subgraph.assert_awaited()


@pytest.mark.asyncio
async def test_auto_mode_defaults_to_hybrid(monkeypatch: pytest.MonkeyPatch) -> None:
    qdrant, graph = _build_mocks()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("auto answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("auto answer"),
    )

    workflow = compile_workflow(qdrant, graph)
    await workflow.ainvoke(_empty_state("auto"))

    qdrant.search_dense.assert_awaited()
    qdrant.search_lexical.assert_awaited()
    graph.traverse_authorized_subgraph.assert_awaited()


@pytest.mark.asyncio
async def test_vector_policy_audit_failure_aborts_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdrant, graph = _build_mocks()
    evidence = RetrievalEvidence(
        evidence_id="ev-1",
        trace_id="tr1",
        tenant_id="t1",
        evidence_type="chunk",
        object_id="chunk-1",
        retriever_id=RetrieverId.VECTOR,
        retrieval_strategy="dense",
        original_rank=0,
        original_score=0.9,
        policy_check_id="pending",
    )
    qdrant.search_dense.return_value = [evidence]
    graph.fga.filter_chunk_ids = AsyncMock(return_value=({"chunk-1"}, [MagicMock()]))
    write_decisions = AsyncMock(side_effect=RuntimeError("audit unavailable"))
    monkeypatch.setattr(
        "securgraph.indexing.postgres_writer.write_policy_decisions", write_decisions
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("answer"),
    )

    workflow = compile_workflow(qdrant, graph)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await workflow.ainvoke(_empty_state("vector"))
