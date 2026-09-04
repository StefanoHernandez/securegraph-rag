from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.domain import (
    Citation,
    Classification,
    QueryScope,
    RetrievalEvidence,
    RetrieverId,
    Subject,
)
from securgraph.domain.policy import (
    PolicyDecision,
    PolicyDecisionValue,
    PolicyObjectType,
)
from securgraph.orchestration.workflow import compile_workflow

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _disable_rls_database_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep workflow mode tests unit-level; RLS SQL is covered by integration tests."""
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.set_tenant_rls_context",
        AsyncMock(),
    )


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
async def test_vector_mode_applies_query_scope_to_both_vector_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdrant, graph = _build_mocks()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("scoped answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("scoped answer"),
    )
    state = _empty_state("vector")
    state["options"]["scope"] = QueryScope(
        max_classification=Classification.INTERNAL,
        document_ids=("doc-1",),
    ).model_dump(mode="json")

    workflow = compile_workflow(qdrant, graph)
    await workflow.ainvoke(state)

    expected_scope = QueryScope(
        max_classification=Classification.INTERNAL,
        document_ids=("doc-1",),
    )
    assert qdrant.search_dense.await_args.kwargs["scope"] == expected_scope
    assert qdrant.search_lexical.await_args.kwargs["scope"] == expected_scope


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
    decision = PolicyDecision(
        decision_id="dec-audit-failure",
        trace_id="tr1",
        tenant_id="t1",
        user_id="user:alice",
        object_type=PolicyObjectType.CHUNK,
        object_id="chunk-1",
        decision=PolicyDecisionValue.ALLOW,
    )
    graph.fga.filter_chunk_ids = AsyncMock(return_value=({"chunk-1"}, [decision]))
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
async def test_vector_retrieval_fails_closed_without_policy_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdrant, graph = _build_mocks()
    graph.fga = None
    qdrant.search_dense.return_value = [
        RetrievalEvidence(
            evidence_id="ev-unverified",
            trace_id="tr1",
            tenant_id="t1",
            evidence_type="chunk",
            object_id="chunk-unverified",
            retriever_id=RetrieverId.VECTOR,
            retrieval_strategy="dense",
            original_rank=0,
            original_score=0.9,
            policy_check_id="unverified",
            text="This text must never reach synthesis.",
        )
    ]
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("untrusted answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("untrusted answer"),
    )

    workflow = compile_workflow(qdrant, graph)

    with pytest.raises(RuntimeError, match="OpenFGA policy adapter"):
        await workflow.ainvoke(_empty_state("vector"))


@pytest.mark.asyncio
async def test_vector_allow_binds_evidence_to_exact_policy_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdrant, graph = _build_mocks()
    evidence = RetrievalEvidence(
        evidence_id="ev-authorized",
        trace_id="tr1",
        tenant_id="t1",
        evidence_type="chunk",
        object_id="chunk-authorized",
        retriever_id=RetrieverId.VECTOR,
        retrieval_strategy="dense",
        original_rank=0,
        original_score=0.9,
        policy_check_id="unverified",
        text="Authorized source text.",
    )
    decision = PolicyDecision(
        decision_id="dec-authorized",
        trace_id="tr1",
        tenant_id="t1",
        user_id="user:alice",
        object_type=PolicyObjectType.CHUNK,
        object_id=evidence.object_id,
        decision=PolicyDecisionValue.ALLOW,
        reason_code="openfga_ok",
    )
    qdrant.search_dense.return_value = [evidence]
    graph.fga.filter_chunk_ids = AsyncMock(
        return_value=({evidence.object_id}, [decision])
    )

    class FakeSessionContext:
        async def __aenter__(self) -> MagicMock:
            session = MagicMock()
            session.commit = AsyncMock()
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    write_decisions = AsyncMock()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.AsyncSessionLocal",
        lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(
        "securgraph.indexing.postgres_writer.write_policy_decisions",
        write_decisions,
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("authorized answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("authorized answer"),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.build_authorized_citations",
        AsyncMock(return_value=()),
    )

    result = await compile_workflow(qdrant, graph).ainvoke(_empty_state("vector"))

    bound_evidence = result["vector_context"][0]
    assert bound_evidence.policy_check_id == decision.decision_id
    persisted = write_decisions.await_args.args[1]
    assert persisted[0].retriever_id == RetrieverId.VECTOR.value


@pytest.mark.asyncio
async def test_synthesis_normalizes_llm_non_answer_to_canonical_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """authorization_denial regression: the retriever surfaces an authorized but
    irrelevant chunk (e.g. a public catalog for a restricted query), the LLM
    honestly replies "No information provided.", and synthesis MUST replace that
    terse non-answer with the canonical, detector-recognized refusal — never
    leak the bare non-answer through to the API caller / QA scorer.
    """
    from securgraph.corpus.qa import _detect_refusal

    qdrant, graph = _build_mocks()
    evidence = RetrievalEvidence(
        evidence_id="ev-public",
        trace_id="tr1",
        tenant_id="t1",
        evidence_type="chunk",
        object_id="chunk-public",
        retriever_id=RetrieverId.VECTOR,
        retrieval_strategy="dense",
        original_rank=0,
        original_score=0.9,
        policy_check_id="unverified",
        text="Public station catalog: coordinates and frequencies only.",
    )
    decision = PolicyDecision(
        decision_id="dec-public",
        trace_id="tr1",
        tenant_id="t1",
        user_id="user:alice",
        object_type=PolicyObjectType.CHUNK,
        object_id=evidence.object_id,
        decision=PolicyDecisionValue.ALLOW,
        reason_code="openfga_ok",
    )
    qdrant.search_dense.return_value = [evidence]
    graph.fga.filter_chunk_ids = AsyncMock(
        return_value=({evidence.object_id}, [decision])
    )

    class FakeSessionContext:
        async def __aenter__(self) -> MagicMock:
            session = MagicMock()
            session.commit = AsyncMock()
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        "securgraph.orchestration.workflow.AsyncSessionLocal",
        lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(
        "securgraph.indexing.postgres_writer.write_policy_decisions", AsyncMock()
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("No information provided."),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("No information provided."),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.build_authorized_citations",
        AsyncMock(return_value=()),
    )

    result = await compile_workflow(qdrant, graph).ainvoke(_empty_state("vector"))

    answer = result["final_answer"]
    assert answer is not None
    assert _detect_refusal(answer.text), (
        f"synthesis must emit a detectable refusal, got {answer.text!r}"
    )
    assert "No information provided" not in answer.text
    assert any("canonical refusal" in w for w in (result.get("warnings") or []))


@pytest.mark.asyncio
async def test_vector_deny_audit_keeps_the_original_retriever_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdrant, graph = _build_mocks()
    evidence = RetrievalEvidence(
        evidence_id="ev-lexical",
        trace_id="tr1",
        tenant_id="t1",
        evidence_type="chunk",
        object_id="chunk-lexical",
        retriever_id=RetrieverId.LEXICAL,
        retrieval_strategy="full_text",
        original_rank=0,
        original_score=1.0,
        policy_check_id="pending",
    )
    qdrant.search_dense.return_value = []
    qdrant.search_lexical.return_value = [evidence]
    decision = PolicyDecision(
        decision_id="dec-lexical",
        trace_id="tr1",
        tenant_id="t1",
        user_id="user:alice",
        object_type=PolicyObjectType.CHUNK,
        object_id="chunk-lexical",
        decision=PolicyDecisionValue.DENY,
        reason_code="unauthorized_rbac",
    )
    graph.fga.filter_chunk_ids = AsyncMock(return_value=(set(), [decision]))

    class FakeSessionContext:
        def __init__(self) -> None:
            self.session = MagicMock()
            self.session.commit = AsyncMock()

        async def __aenter__(self) -> MagicMock:
            return self.session

        async def __aexit__(self, *args: object) -> None:
            return None

    sessions = [FakeSessionContext(), FakeSessionContext(), FakeSessionContext()]
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.AsyncSessionLocal",
        lambda: sessions.pop(0),
    )
    monkeypatch.setattr(
        "securgraph.indexing.postgres_writer.fetch_chunk_text_hashes",
        AsyncMock(return_value={"chunk-lexical": "sha256:chunk"}),
    )
    write_decisions = AsyncMock()
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
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.build_authorized_citations",
        AsyncMock(return_value=()),
    )

    workflow = compile_workflow(qdrant, graph)
    await workflow.ainvoke(_empty_state("vector"))

    persisted = write_decisions.await_args.args[1]
    assert persisted[0].retriever_id == "lexical"
    assert persisted[0].resource_hash == "sha256:chunk"


@pytest.mark.asyncio
async def test_synthesis_attaches_deterministic_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qdrant, graph = _build_mocks()
    decision = PolicyDecision(
        decision_id="dec-1",
        trace_id="tr1",
        tenant_id="t1",
        user_id="user:alice",
        object_type=PolicyObjectType.CHUNK,
        object_id="chunk-1",
        decision=PolicyDecisionValue.ALLOW,
    )
    graph.fga.filter_chunk_ids = AsyncMock(return_value=({"chunk-1"}, [decision]))
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
        async def __aenter__(self) -> MagicMock:
            session = MagicMock()
            session.commit = AsyncMock()
            return session

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
        "securgraph.orchestration.workflow.set_tenant_rls_context",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.build_authorized_citations",
        fake_build_citations,
    )
    monkeypatch.setattr(
        "securgraph.indexing.postgres_writer.write_policy_decisions",
        AsyncMock(),
    )

    workflow = compile_workflow(qdrant, graph)
    result = await workflow.ainvoke(_empty_state("vector"))

    final_answer = result["final_answer"]
    assert final_answer.citations[0].source_uri == "documents/01_policy.md"


@pytest.mark.asyncio
async def test_citation_lookup_binds_tenant_rls_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from securgraph.orchestration.workflow import _build_citations_for_state

    session = MagicMock()

    class FakeSessionContext:
        async def __aenter__(self) -> MagicMock:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    set_context = AsyncMock()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.AsyncSessionLocal",
        lambda: FakeSessionContext(),
    )
    monkeypatch.setattr("securgraph.orchestration.workflow.set_tenant_rls_context", set_context)
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.build_authorized_citations",
        AsyncMock(return_value=()),
    )

    await _build_citations_for_state(_empty_state("vector"))

    set_context.assert_awaited_once_with(session, tenant_id="t1", is_super_admin=False)


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


@pytest.mark.asyncio
async def test_global_synthesis_returns_an_answer_without_unbound_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global branch used to crash on exactly the successful case.

    `warnings` was assigned only inside `if normalized is not None`, the
    branch taken when the draft is NOT an answer. Whenever the model produced
    a real answer, the return statement read an unbound local and every
    successful global query became a 500. No unit test covered this branch,
    which is why it survived.
    """
    qdrant, graph = _build_mocks()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda *a, **kw: _fake_adapter("A real synthesized answer about Stefano."),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **kw: _fake_adapter("A real synthesized answer about Stefano."),
    )

    class _StubCommunityRetriever:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def retrieve_communities(self, *args: Any, **kwargs: Any) -> list[Any]:
            return []

    monkeypatch.setattr(
        "securgraph.indexing.global_retriever.AuthorizedCommunityRetriever",
        _StubCommunityRetriever,
    )

    state = _empty_state("hybrid")
    state["options"]["query_type"] = "global"

    workflow = compile_workflow(qdrant, graph)
    result = await workflow.ainvoke(state)

    assert result["final_answer"] is not None
    assert result["final_answer"].text == "A real synthesized answer about Stefano."
    # The happy path carries no synthesis warning: nothing was refused.
    assert not [w for w in result["warnings"] if "canonical refusal" in w]
