from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.domain import Citation, RetrievalEvidence, RetrieverId, Subject
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


@pytest.mark.asyncio
async def test_synthesis_attaches_deterministic_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdrant, graph = _build_mocks()
    graph.fga = None
    evidence = RetrievalEvidence(
        evidence_id="ev-source",
        trace_id="tr1",
        tenant_id="t1",
        evidence_type="chunk",
        object_id="chunk-1",
        retriever_id=RetrieverId.VECTOR,
        retrieval_strategy="dense",
        original_rank=0,
        original_score=0.9,
        policy_check_id="dec-1",
        text="Policy source text.",
    )
    qdrant.search_dense.return_value = [evidence]
    qdrant.search_lexical.return_value = []

    class FakeSessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            return None

    async def fake_build_citations(*args: object, **kwargs: object) -> tuple[Citation, ...]:
        assert kwargs["tenant_id"] == "t1"
        vector_context = kwargs["vector_context"]
        assert isinstance(vector_context, list)
        assert vector_context[0].object_id == "chunk-1"
        return (
            Citation(
                evidence_id="ev-source",
                document_id="doc-1",
                chunk_id="chunk-1",
                label="Policy",
                source_uri="documents/01_policy.md",
            ),
        )

    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("answer without structured citations"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("answer without structured citations"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.AsyncSessionLocal",
        lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.build_authorized_citations",
        fake_build_citations,
    )

    workflow = compile_workflow(qdrant, graph)
    result = await workflow.ainvoke(_empty_state("vector"))

    final_answer = result["final_answer"]
    assert final_answer.citations[0].source_uri == "documents/01_policy.md"


def test_auto_route_identifier_query_boosts_lexical() -> None:
    from securgraph.orchestration.workflow import auto_route

    routed = auto_route(
        'Find ticket A-12 about "budget freeze"', {"retrieval_mode": "auto", "top_k": 5}
    )
    assert routed["retrieval_mode"] == "hybrid"
    assert routed["top_k"] == 15


def test_auto_route_relational_query_deepens_graph() -> None:
    from securgraph.orchestration.workflow import auto_route

    routed = auto_route(
        "Who is related to the Aurora project?",
        {"retrieval_mode": "auto", "max_graph_depth": 2},
    )
    assert routed["retrieval_mode"] == "hybrid"
    assert routed["max_graph_depth"] == 3
    # Hard cap at 4.
    capped = auto_route(
        "Who is related to Aurora?", {"retrieval_mode": "auto", "max_graph_depth": 4}
    )
    assert capped["max_graph_depth"] == 4


def test_auto_route_plain_query_defaults_to_hybrid() -> None:
    from securgraph.orchestration.workflow import auto_route

    opts = {"retrieval_mode": "auto", "top_k": 5, "max_graph_depth": 2}
    routed = auto_route("What is the security policy?", opts)
    assert routed["retrieval_mode"] == "hybrid"
    assert routed["top_k"] == 5
    assert routed["max_graph_depth"] == 2
    # Explicit modes pass through untouched (same object).
    explicit = {"retrieval_mode": "vector", "top_k": 5}
    assert auto_route('Find "exact" A-12', explicit) is explicit
