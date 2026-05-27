from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import pytest

from securgraph.adapters.community_generator import generate_hierarchical_communities
from securgraph.config import get_settings
from securgraph.domain import Subject
from securgraph.domain.community import Community
from securgraph.domain.policy import PolicyDecisionValue, OpenFGAPolicyAdapter
from securgraph.indexing.global_retriever import AuthorizedCommunityRetriever
from securgraph.orchestration.workflow import compile_workflow, QueryState

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_community_generation_and_partitioning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Louvain partitioning, Ollama JSON summary prompting, and Neo4j Community writes."""
    settings = get_settings()
    settings.feature_communities_enabled = True

    # 1. Mock Neo4j session returns
    mock_entities = [
        {"name": "SSL"},
        {"name": "TLS"},
        {"name": "Network"},
        {"name": "Security"},
        {"name": "Policy"},
    ]
    mock_relations = [
        {"source": "SSL", "target": "TLS", "type": "RELATED_TO", "confidence": 0.9},
        {"source": "Network", "target": "Security", "type": "RELATED_TO", "confidence": 0.8},
        {"source": "Security", "target": "Policy", "type": "RELATED_TO", "confidence": 0.75},
    ]

    mock_run = AsyncMock()
    
    mock_entities_result = MagicMock()
    mock_entities_result.data = AsyncMock(return_value=mock_entities)

    mock_relations_result = MagicMock()
    mock_relations_result.data = AsyncMock(return_value=mock_relations)

    async def mock_run_fn(cypher: str, **kwargs) -> Any:
        if "MATCH (e:Entity" in cypher:
            return mock_entities_result
        elif "MATCH (src:Entity" in cypher:
            return mock_relations_result
        else:
            return MagicMock()

    mock_run.side_effect = mock_run_fn

    class MockSession:
        async def __aenter__(self) -> MockSession:
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
            pass
        async def run(self, cypher: str, **kwargs) -> Any:
            return await mock_run(cypher, **kwargs)

    mock_driver = MagicMock()
    mock_driver.session = lambda: MockSession()

    # 2. Mock Ollama Client for JSON community summaries
    mock_chat_response = MagicMock()
    mock_chat_response.message.content = (
        '{\n'
        '  "title": "Protocolli di Crittografia SSL/TLS",\n'
        '  "summary": "Analisi delle connessioni di rete sicure basate sui protocolli di crittografia."\n'
        '}'
    )
    
    mock_ollama_client = AsyncMock()
    mock_ollama_client.chat.return_value = mock_chat_response

    monkeypatch.setattr(
        "securgraph.adapters.community_generator.get_ollama_client",
        lambda: mock_ollama_client
    )

    # 3. Run the generator
    await generate_hierarchical_communities(driver=mock_driver, tenant_id="test_tenant_comm")

    # Assert that it fetched nodes and edges and purged old records
    assert mock_run.call_count >= 3
    assert "MATCH (e:Entity" in mock_run.call_args_list[0][0][0]
    assert "MATCH (src:Entity" in mock_run.call_args_list[1][0][0]
    assert "DETACH DELETE c" in mock_run.call_args_list[2][0][0]

    # Assert that it performed community merges with members
    comm_merge_queries = [call[0][0] for call in mock_run.call_args_list[3:]]
    assert any("MERGE (c:Community" in q for q in comm_merge_queries)
    assert any("MERGE (c)-[:CONTAINS_ENTITY]" in q for q in comm_merge_queries)

    # Reset feature flag
    settings.feature_communities_enabled = False


@pytest.mark.asyncio
async def test_global_retriever_and_rebac_filtering() -> None:
    """Verify AuthorizedCommunityRetriever fetches Neo4j communities and filters via OpenFGA ReBAC."""
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
    """Verify that CompiledStateGraph routes to global community retrieval and synthesizes correctly."""
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

    # Mock Ollama chat synthesis response
    mock_synthesis_response = MagicMock()
    mock_synthesis_response.message.content = "Risposta sintetica globale basata sulle comunità."
    mock_synthesis_client = AsyncMock()
    mock_synthesis_client.chat.return_value = mock_synthesis_response

    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_ollama_client",
        lambda: mock_synthesis_client
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
