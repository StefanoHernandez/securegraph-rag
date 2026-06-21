from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.adapters.community_generator import generate_hierarchical_communities
from securgraph.config import get_settings
from securgraph.domain import Subject
from securgraph.domain.community import Community
from securgraph.domain.policy import OpenFGAPolicyAdapter
from securgraph.indexing.global_retriever import AuthorizedCommunityRetriever
from securgraph.orchestration.workflow import QueryState, compile_workflow

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_community_generation_partitions_by_policy_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edges from two access classes must produce communities tagged per class,
    and no community may mix entities from different classes."""
    settings = get_settings()
    settings.feature_communities_enabled = True

    mock_chunks = [
        {"chunk_id": "c1", "owner_id": "alice", "policy_tags": [], "direct_viewers": []},
        {"chunk_id": "c2", "owner_id": "bob", "policy_tags": [], "direct_viewers": []},
    ]
    mock_relations = [
        {"source": "SSL", "target": "TLS", "type": "RELATED_TO", "confidence": 0.9,
         "source_chunk_ids": ["c1"]},
        {"source": "Payroll", "target": "Bank", "type": "RELATED_TO", "confidence": 0.8,
         "source_chunk_ids": ["c2"]},
    ]

    merged_communities: list[dict] = []
    contains_calls: list[dict] = []

    async def mock_run_fn(cypher: str, **kwargs):  # type: ignore[no-untyped-def]
        result = MagicMock()
        if "MATCH (c:Chunk" in cypher:
            result.data = AsyncMock(return_value=mock_chunks)
        elif "MATCH (src:Entity" in cypher:
            result.data = AsyncMock(return_value=mock_relations)
        elif "MERGE (c:Community" in cypher:
            merged_communities.append(kwargs)
            result.data = AsyncMock(return_value=[])
        elif "CONTAINS_ENTITY" in cypher:
            contains_calls.append(kwargs)
            result.data = AsyncMock(return_value=[])
        else:
            result.data = AsyncMock(return_value=[])
        return result

    class MockSession:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self
        async def __aexit__(self, *a):  # type: ignore[no-untyped-def]
            pass
        async def run(self, cypher: str, **kwargs):  # type: ignore[no-untyped-def]
            return await mock_run_fn(cypher, **kwargs)

    mock_driver = MagicMock()
    mock_driver.session = lambda: MockSession()

    mock_adapter = AsyncMock()
    mock_adapter.chat.return_value = '{"title": "T", "summary": "S"}'
    monkeypatch.setattr(
        "securgraph.adapters.community_generator.get_llm_adapter", lambda: mock_adapter
    )

    await generate_hierarchical_communities(driver=mock_driver, tenant_id="t1")

    assert merged_communities, "expected community writes"
    keys = {m["policy_class_key"] for m in merged_communities}
    assert len(keys) == 2, f"expected 2 distinct classes, got {keys}"

    class_a = {"SSL", "TLS"}
    class_b = {"Payroll", "Bank"}
    for m in merged_communities:
        members = set(m["member_entities"])
        assert not (members & class_a and members & class_b), "community spans classes"

    settings.feature_communities_enabled = False


@pytest.mark.asyncio
async def test_global_retriever_and_rebac_filtering() -> None:
    """Verify AuthorizedCommunityRetriever fetches Neo4j communities and filters via ReBAC."""
    # 1. Mock Neo4j communities query return
    mock_db_records = [
        {
            "community_id": "comm_l1_0_tenant_test",
            "title": "SSL Protocols",
            "summary": "Security guidelines for SSL.",
            "level": 1,
            "member_entities": ["SSL", "TLS"],
        }
    ]
    mock_run = AsyncMock()
    mock_result = MagicMock()
    mock_result.data = AsyncMock(return_value=mock_db_records)
    mock_run.return_value = mock_result

    class MockSession:
        async def __aenter__(self) -> MockSession:
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
            pass
        async def run(self, cypher: str, **kwargs) -> Any:
            return await mock_run(cypher, **kwargs)

    mock_driver = MagicMock()
    mock_driver.session = lambda: MockSession()

    # 2. Mock OpenFGA policy check
    # We will simulate allowed = True
    mock_fga_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.allowed = True
    mock_fga_client.check.return_value = mock_response

    mock_policy_adapter = OpenFGAPolicyAdapter(mock_fga_client)

    retriever = AuthorizedCommunityRetriever(driver=mock_driver, fga=mock_policy_adapter)

    subject = Subject(tenant_id="tenant_test", user_id="user:stefano")

    # 3. Retrieve
    res = await retriever.retrieve_communities(subject=subject, level=1)

    assert len(res) == 1
    assert res[0].community_id == "comm_l1_0_tenant_test"
    assert res[0].title == "SSL Protocols"
    assert res[0].level == 1
    assert "SSL" in res[0].member_entities


@pytest.mark.asyncio
async def test_global_workflow_routing_and_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CompiledStateGraph routes to global community retrieval and synthesizes correctly."""
    settings = get_settings()
    settings.feature_communities_enabled = True

    # Mock AuthorizedCommunityRetriever returned summaries
    mock_summary = Community(
        community_id="comm_l1_test",
        tenant_id="tenant_global_wf",
        title="Protocollo di Sicurezza",
        summary="Panoramica delle linee guida SSL e TLS.",
        level=1,
        member_entities=("SSL", "TLS"),
    )

    mock_retrieve = AsyncMock(return_value=[mock_summary])
    monkeypatch.setattr(
        "securgraph.indexing.global_retriever.AuthorizedCommunityRetriever.retrieve_communities",
        mock_retrieve
    )

    mock_adapter = AsyncMock()
    mock_adapter.chat.return_value = "Risposta sintetica globale basata sulle comunità."
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter",
        lambda: mock_adapter
    )

    # 1. Compile LangGraph workflow
    qdrant = MagicMock()
    graph = MagicMock()
    graph.driver = MagicMock()
    graph.fga = MagicMock()

    workflow = compile_workflow(qdrant, graph)

    state_input: QueryState = {
        "query": "Quali sono le principali linee guida di sicurezza?",
        "subject": Subject(tenant_id="tenant_global_wf", user_id="user:stefano"),
        "options": {"query_type": "global", "consensus_threshold": 0.7},
        "trace_id": "trace_global_wf_test",
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

    # 2. Invoke the compiled LangGraph workflow end-to-end!
    result = await workflow.ainvoke(state_input)
    assert result is not None

    # Assert correct global routing outputs
    assert result["graph_draft"] is not None
    assert "Protocollo di Sicurezza" in result["graph_draft"]
    assert result["consensus_report"] is None
    assert result["final_answer"] is not None
    assert result["final_answer"].text == "Risposta sintetica globale basata sulle comunità."

    # Clean up
    settings.feature_communities_enabled = False
