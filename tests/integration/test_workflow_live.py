import pytest
from typing import Any
from pydantic import ConfigDict
from securgraph.adapters.ollama import get_ollama_client
from securgraph.domain import Subject, VerifierStatus, NLILabel
from securgraph.domain.retrieval import RetrievalEvidence, RetrieverId
from securgraph.domain.graph import GraphPath
from securgraph.orchestration.workflow import compile_workflow, QueryState

pytestmark = pytest.mark.integration

@pytest.fixture(autouse=True)
def clear_ollama_client_cache():
    get_ollama_client.cache_clear()
    yield
    get_ollama_client.cache_clear()

class MockRetrievalEvidence(RetrievalEvidence):
    model_config = ConfigDict(extra="allow")
    text: str

    def __str__(self) -> str:
        return self.text

class MockGraphPath(GraphPath):
    model_config = ConfigDict(extra="allow")
    text: str

    def __str__(self) -> str:
        return self.text

class MockQdrant:
    async def search_dense(self, tenant_id: str, query: str, limit: int, trace_id: str) -> list[RetrievalEvidence]:
        return [
            MockRetrievalEvidence(
                evidence_id="ev_dense_1",
                trace_id=trace_id,
                tenant_id=tenant_id,
                evidence_type="chunk",
                object_id="chk_1",
                retriever_id=RetrieverId.VECTOR,
                retrieval_strategy="dense",
                original_rank=0,
                original_score=0.9,
                policy_check_id="policy_1",
                text="Stefano is a software engineer on the Advanced Agentic Coding team based in Milan."
            )
        ]

    async def search_lexical(self, tenant_id: str, query: str, limit: int, trace_id: str) -> list[RetrievalEvidence]:
        return []

class MockGraph:
    async def traverse_authorized_subgraph(
        self, session: Any, subject: Subject, seed_entities: list[str], trace_id: str, max_depth: int
    ) -> list[GraphPath]:
        return [
            MockGraphPath(
                path_id="path_1",
                nodes=("Stefano", "Milan"),
                edges=("LIVES_IN",),
                source_chunk_ids=("chk_2",),
                confidence=1.0,
                text="Stefano is a software engineer based in Milan on the Advanced Agentic Coding team."
            )
        ]

@pytest.mark.asyncio
async def test_workflow_live_compile_and_run() -> None:
    qdrant = MockQdrant()
    graph = MockGraph()
    workflow = compile_workflow(qdrant, graph)

    subject = Subject(user_id="user:stefano", tenant_id="tenant_1")
    state_input: QueryState = {
        "query": "Who is Stefano and what is his role?",
        "subject": subject,
        "options": {"consensus_threshold": 0.6},
        "trace_id": "trace_workflow_test_123",
        "vector_context": [],
        "vector_draft": None,
        "vector_claims": [],
        "graph_context": [],
        "graph_draft": None,
        "graph_claims": [],
        "consensus_report": None,
        "final_answer": None,
        "warnings": []
    }

    # Execute the LangGraph workflow compiled graph
    result = await workflow.ainvoke(state_input)

    assert result["vector_draft"] is not None
    assert result["graph_draft"] is not None
    assert len(result["vector_claims"]) > 0
    assert len(result["graph_claims"]) > 0
    
    report = result["consensus_report"]
    assert report is not None
    assert report.trace_id == "trace_workflow_test_123"
    assert report.agreement_score >= 0.0
    
    final_answer = result["final_answer"]
    assert final_answer is not None
    assert "stefano" in final_answer.text.lower()
