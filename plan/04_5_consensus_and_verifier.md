# Milestone 4.5 — Consensus & Verifier (C2) Implementation Plan (Live Integration First)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement independent vector and graph retrieval state loops, claim decomposition, and NLI agreement scoring integrated with LangGraph to gate high-trust answers, tested against **live Docker backends and live Ollama Gemma model**.

**Architecture:** We construct a LangGraph `StateGraph` running independent vector and graph retrieval channels, synthesizing drafts, decomposing drafts into claims via local Ollama structured schemas, calculating bidirectional NLI agreement and Jaccard entity overlap, and compiling a composite `ConsensusReport` that gates a `high_trust` response.

**Tech Stack:** LangGraph Python, FastAPI, Ollama Python SDK (`gemma4:e2b`), Pydantic v2, Qdrant, Neo4j, OpenFGA, PostgreSQL, pytest, Ruff, MyPy.

---

## Files Map

- **Modify**:
  - `src/securgraph/domain/query.py`: Add `consensus_threshold` option.
  - `src/securgraph/config.py`: Add global consensus threshold settings.
  - `src/securgraph/adapters/ollama.py`: Add Claim Decomposition and NLI Judging functions using live Ollama.
  - `src/securgraph/domain/consensus.py`: Add the consensus calculation engine function.
  - `src/securgraph/api/main.py`: Register the new `/query` router.
- **Create**:
  - `src/securgraph/orchestration/__init__.py`
  - `src/securgraph/orchestration/workflow.py`: Compile the LangGraph `StateGraph` and its nodes with real dependencies.
  - `src/securgraph/api/routes/query.py`: Expose the `POST /query` endpoint.
  - `tests/integration/test_ollama_consensus_live.py`: Live integration test for Ollama claims extraction and NLI.
  - `tests/integration/test_consensus_calc.py`: Unit test for the mathematical consensus calculation engine.
  - `tests/integration/test_workflow_live.py`: Live integration test for the LangGraph workflow running against Qdrant, Neo4j, and Ollama.
  - `tests/integration/test_api_query_live.py`: Live integration test for the `/query` endpoint.

---

### Prerequisites: Ensure Live Backends are Online

- [ ] **Step 1: Start Docker containers**
  Run: `make up`
  Expected: All containers (Postgres, Qdrant, Neo4j, OpenFGA, Jaeger) start up and report healthy.

- [ ] **Step 2: Pull host-native Ollama models**
  Run: `make pull-models`
  Expected: Ollama pulls `gemma4:e2b` and pre-downloads embedding models.

---

### Task 1: Extend Query Options and Configuration Settings

**Files:**
- Modify: `src/securgraph/domain/query.py`
- Modify: `src/securgraph/config.py`
- Test: `tests/unit/test_domain.py:188-193`

- [ ] **Step 1: Write the failing test**
  Modify `tests/unit/test_domain.py` around line 188 to verify the new option `consensus_threshold` exists with a default value of `0.7`.
  ```python
  def test_query_request_defaults() -> None:
      q = QueryRequest(tenant_id="mrd", user_id="user:alice", query="hello world")
      assert q.options.retrieval_mode == "auto"
      assert q.options.max_graph_depth == 2
      assert q.options.consensus_threshold == 0.7  # New check
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `.venv/bin/pytest tests/unit/test_domain.py::test_query_request_defaults -v`
  Expected: FAIL with `AttributeError: 'QueryOptions' object has no attribute 'consensus_threshold'`

- [ ] **Step 3: Write minimal implementation**
  Modify `src/securgraph/domain/query.py` around line 28 to add the field:
  ```python
  class QueryOptions(SGModel):
      include_explanation: bool = False
      max_graph_depth: int = Field(default=2, ge=1, le=4)
      retrieval_mode: RetrievalMode = "auto"
      top_k: int = Field(default=10, ge=1, le=100)
      consensus_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
  ```
  Also modify `src/securgraph/config.py` around line 35 to add default consensus threshold to the settings schema:
  ```python
  class Settings(BaseSettings):
      # ... existing fields
      consensus_threshold: float = 0.7
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `.venv/bin/pytest tests/unit/test_domain.py::test_query_request_defaults -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/securgraph/domain/query.py src/securgraph/config.py tests/unit/test_domain.py
  git commit -m "feat(m4.5): add consensus_threshold to QueryOptions and configuration settings"
  ```

---

### Task 2: Implement Claim Extraction and NLI Judging Adapters calling Live Ollama

**Files:**
- Modify: `src/securgraph/adapters/ollama.py`
- Create: `tests/integration/test_ollama_consensus_live.py`

- [ ] **Step 1: Write the live integration test**
  Create `tests/integration/test_ollama_consensus_live.py` that makes real network calls to the running local Ollama model to verify claim extraction and NLI classification:
  ```python
  import pytest
  from securgraph.adapters.ollama import extract_claims, judge_nli

  pytestmark = pytest.mark.integration

  @pytest.mark.asyncio
  async def test_extract_claims_live() -> None:
      text = "Marco works as a medical doctor in Milan. He has a degree in medicine."
      res = await extract_claims(text)
      assert len(res.claims) >= 1
      # Check that claims decompose facts cleanly
      assert any("doctor" in c.text.lower() or "medicine" in c.text.lower() for c in res.claims)
      assert any("marco" in c.text.lower() or "doctor" in c.text.lower() for c in res.claims)

  @pytest.mark.asyncio
  async def test_judge_nli_live() -> None:
      claim = "Marco is a doctor."
      reference = "Marco works as a medical doctor in Milan."
      res = await judge_nli(claim, reference)
      assert res.label == "entailment"
      assert res.confidence >= 0.5
      
      claim_false = "Marco is a lawyer."
      res_false = await judge_nli(claim_false, reference)
      assert res_false.label in ("contradiction", "neutral")
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `.venv/bin/pytest tests/integration/test_ollama_consensus_live.py -v`
  Expected: FAIL with `ImportError` or failure because functions are not defined.

- [ ] **Step 3: Write implementation using structured JSON outputs**
  Modify `src/securgraph/adapters/ollama.py` to add new models and functions:
  ```python
  from pydantic import BaseModel, Field
  from typing import Literal

  class AtomicClaim(BaseModel):
      claim_id: str = Field(description="Unique ID, e.g. 'c0', 'c1'")
      text: str = Field(description="Self-contained, atomic, factual claim.")
      entities: list[str] = Field(default_factory=list, description="Key entities mentioned in the claim.")

  class ClaimDecomposition(BaseModel):
      claims: list[AtomicClaim]

  class NLIResult(BaseModel):
      label: Literal["entailment", "neutral", "contradiction"]
      confidence: float = Field(ge=0.0, le=1.0)
      reason: str = Field(description="Brief explanation.")

  async def extract_claims(text: str) -> ClaimDecomposition:
      """Decompose text into atomic claims with entities using live Ollama structured JSON."""
      client = get_ollama_client()
      settings = get_settings()
      
      prompt = (
          "Decompose the following text into independent, atomic, self-contained factual claims and extract key entities.\n"
          "Text: " + text
      )
      
      response = await client.chat(
          model=settings.llm_model,
          messages=[
              {"role": "system", "content": "You are a precise claim decomposition assistant. Respond strictly in JSON matching the schema."},
              {"role": "user", "content": prompt}
          ],
          format="json"
      )
      content = response.message.content or "{}"
      return ClaimDecomposition.model_validate_json(content)

  async def judge_nli(claim: str, reference: str) -> NLIResult:
      """Evaluate claim against reference text using live Ollama NLI classification."""
      client = get_ollama_client()
      settings = get_settings()
      
      prompt = (
          f"Analyze whether the Claim is supported by the Reference Context.\n"
          f"Claim: {claim}\n"
          f"Reference Context: {reference}"
      )
      
      response = await client.chat(
          model=settings.llm_model,
          messages=[
              {"role": "system", "content": "You are an expert NLI judge classifying relationships as entailment, neutral, or contradiction. Respond strictly in JSON matching the schema."},
              {"role": "user", "content": prompt}
          ],
          format="json"
      )
      content = response.message.content or "{}"
      return NLIResult.model_validate_json(content)
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `.venv/bin/pytest tests/integration/test_ollama_consensus_live.py -v`
  Expected: PASS (requires active Ollama container)

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/securgraph/adapters/ollama.py tests/integration/test_ollama_consensus_live.py
  git commit -m "feat(m4.5): implement claim extraction and NLI judging Ollama adapters with live tests"
  ```

---

### Task 3: Implement Consensus Calculation Engine

**Files:**
- Modify: `src/securgraph/domain/consensus.py`
- Create: `tests/integration/test_consensus_calc.py`

- [ ] **Step 1: Write the test**
  Create `tests/integration/test_consensus_calc.py` to assert mathematical invariants of the consensus calculator:
  ```python
  import pytest
  from securgraph.domain.consensus import calculate_consensus, NLILabel, VerifierStatus, Claim
  from securgraph.domain.base import TraceId

  pytestmark = pytest.mark.unit

  def test_calculate_consensus_full_entailment() -> None:
      trace_id = "trace_123"
      vector_claims = (
          Claim(claim_id="c0", text="A is true", nli_label=NLILabel.ENTAILMENT, nli_confidence=0.9, verifier_status=VerifierStatus.SUPPORTED),
      )
      graph_claims = (
          Claim(claim_id="c1", text="B is true", nli_label=NLILabel.ENTAILMENT, nli_confidence=0.8, verifier_status=VerifierStatus.SUPPORTED),
      )
      
      # Overlap: {"a", "true"} and {"b", "true"} => Intersection: {"true"}, Union: {"a", "b", "true"} => Jaccard: 1/3 = 0.3333
      # claim_nli_score: 1.0 (both entailment)
      # agreement_score: 0.4 * 0.3333 + 0.6 * 1.0 = 0.7333
      # high_trust: True (since 0.7333 >= 0.7)
      
      report = calculate_consensus(
          trace_id=trace_id,
          vector_claims=vector_claims,
          graph_claims=graph_claims,
          vector_entities={"a", "true"},
          graph_entities={"b", "true"},
          threshold=0.7
      )
      
      assert report.trace_id == trace_id
      assert abs(report.entity_overlap_jaccard - 0.3333) < 0.001
      assert report.claim_nli_score == 1.0
      assert report.agreement_score > 0.73
      assert report.high_trust is True
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `.venv/bin/pytest tests/integration/test_consensus_calc.py -v`
  Expected: FAIL with `ImportError: cannot import name 'calculate_consensus'`

- [ ] **Step 3: Write minimal implementation**
  Add the calculation function at the bottom of `src/securgraph/domain/consensus.py`:
  ```python
  def calculate_consensus(
      trace_id: TraceId,
      vector_claims: tuple[Claim, ...],
      graph_claims: tuple[Claim, ...],
      vector_entities: set[str],
      graph_entities: set[str],
      threshold: float = 0.7,
  ) -> ConsensusReport:
      """Calculate bidirectional NLI score and Jaccard similarity to compile ConsensusReport."""
      # 1. Entity Overlap
      if not vector_entities and not graph_entities:
          jaccard = 1.0
      else:
          jaccard = len(vector_entities & graph_entities) / len(vector_entities | graph_entities)

      # 2. NLI Agreement Average (entailment = 1.0, others = 0.0)
      total_claims = len(vector_claims) + len(graph_claims)
      if total_claims == 0:
          nli_score = 1.0
      else:
          entailed_count = 0
          for c in vector_claims:
              if c.nli_label == NLILabel.ENTAILMENT:
                  entailed_count += 1
          for c in graph_claims:
              if c.nli_label == NLILabel.ENTAILMENT:
                  entailed_count += 1
          nli_score = entailed_count / total_claims

      # 3. Composite score
      agreement = 0.4 * jaccard + 0.6 * nli_score
      high_trust = agreement >= threshold

      return ConsensusReport(
          trace_id=trace_id,
          vector_claims=vector_claims,
          graph_claims=graph_claims,
          entity_overlap_jaccard=round(jaccard, 4),
          claim_nli_score=round(nli_score, 4),
          agreement_score=round(agreement, 4),
          high_trust=high_trust,
      )
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `.venv/bin/pytest tests/integration/test_consensus_calc.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/securgraph/domain/consensus.py tests/integration/test_consensus_calc.py
  git commit -m "feat(m4.5): implement consensus report calculation engine"
  ```

---

### Task 4: Build LangGraph Orchestration State Loops with Live Integration

**Files:**
- Create: `src/securgraph/orchestration/__init__.py`
- Create: `src/securgraph/orchestration/workflow.py`
- Create: `tests/integration/test_workflow_live.py`

- [ ] **Step 1: Write the live integration test**
  Create `tests/integration/test_workflow_live.py` to compile and run a full StateGraph execution using actual database sessions and live Ollama connections:
  ```python
  import pytest
  from securgraph.domain import Subject, RetrieverId
  from securgraph.orchestration.workflow import compile_workflow
  from securgraph.api.dependencies import get_qdrant_client, get_neo4j_driver
  from securgraph.indexing.vector_retriever import QdrantRetriever
  from securgraph.indexing.graph_retriever import AuthorizedGraphRetriever
  from securgraph.adapters.embeddings import BGEM3EmbeddingAdapter
  from securgraph.domain import OpenFGAPolicyAdapter

  pytestmark = pytest.mark.integration

  @pytest.mark.asyncio
  async def test_workflow_runs_live() -> None:
      # Initialize actual connections
      qclient = get_qdrant_client()
      embedder = BGEM3EmbeddingAdapter()
      qdrant_retriever = QdrantRetriever(qclient, embedder)
      
      neo_driver = get_neo4j_driver()
      fga_adapter = OpenFGAPolicyAdapter()
      graph_retriever = AuthorizedGraphRetriever(neo_driver, fga_adapter)

      workflow = compile_workflow(
          qdrant=qdrant_retriever,
          graph=graph_retriever
      )

      initial_state = {
          "query": "Is system A secure?",
          "subject": Subject(user_id="user:alice", tenant_id="mrd"),
          "options": {"consensus_threshold": 0.7, "max_graph_depth": 2, "top_k": 5},
          "trace_id": "live_trace_123",
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

      res = await workflow.ainvoke(initial_state)
      assert res["final_answer"] is not None
      assert res["consensus_report"] is not None
      assert len(res["final_answer"].text) > 0
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `.venv/bin/pytest tests/integration/test_workflow_live.py -v`
  Expected: FAIL with `ModuleNotFoundError` because files are missing.

- [ ] **Step 3: Write implementation with live Ollama calls**
  Create `src/securgraph/orchestration/__init__.py` and `src/securgraph/orchestration/workflow.py`:
  ```python
  from __future__ import annotations
  from typing import Any
  from langgraph.graph import StateGraph, START, END
  from securgraph.domain import Subject, Claim, NLILabel, VerifierStatus, Answer, RetrieverId
  from securgraph.domain.consensus import calculate_consensus
  from securgraph.domain.query import QueryState
  from securgraph.adapters.ollama import extract_claims, judge_nli, get_ollama_client
  from securgraph.config import get_settings

  def compile_workflow(qdrant: Any, graph: Any) -> Any:
      builder = StateGraph(QueryState)

      async def vector_retrieval_node(state: QueryState) -> dict[str, Any]:
          subject: Subject = state["subject"]
          q: str = state["query"]
          trace: str = state["trace_id"]
          limit = state["options"].get("top_k", 10)
          
          dense = await qdrant.search_dense(subject.tenant_id, q, limit=limit, trace_id=trace)
          lexical = await qdrant.search_lexical(subject.tenant_id, q, limit=limit, trace_id=trace)
          
          return {"vector_context": dense + lexical}

      async def graph_retrieval_node(state: QueryState) -> dict[str, Any]:
          seeds = [word.strip(",.?!") for word in state["query"].split() if word[0].isupper()]
          paths = await graph.traverse_authorized_subgraph(
              session=None,
              subject=state["subject"],
              seed_entities=seeds,
              trace_id=state["trace_id"],
              max_depth=state["options"].get("max_graph_depth", 2)
          )
          
          # Convert graph paths to retrieval evidence
          evidence = []
          for idx, path in enumerate(paths):
              evidence.append(path)
          return {"graph_context": evidence}

      async def vector_draft_node(state: QueryState) -> dict[str, Any]:
          client = get_ollama_client()
          settings = get_settings()
          context_text = "\n".join([str(ev) for ev in state["vector_context"]])
          prompt = f"Answer using vector sources only:\nContext: {context_text}\nQuery: {state['query']}"
          
          response = await client.chat(
              model=settings.llm_model,
              messages=[
                  {"role": "system", "content": "You are a secure factual RAG assistant. Output a concise answer based ONLY on the context."},
                  {"role": "user", "content": prompt}
              ]
          )
          return {"vector_draft": response.message.content or ""}

      async def graph_draft_node(state: QueryState) -> dict[str, Any]:
          client = get_ollama_client()
          settings = get_settings()
          context_text = "\n".join([str(ev) for ev in state["graph_context"]])
          prompt = f"Answer using graph sources only:\nContext: {context_text}\nQuery: {state['query']}"
          
          response = await client.chat(
              model=settings.llm_model,
              messages=[
                  {"role": "system", "content": "You are a secure factual RAG assistant. Output a concise answer based ONLY on the context."},
                  {"role": "user", "content": prompt}
              ]
          )
          return {"graph_draft": response.message.content or ""}

      async def vector_claim_extraction_node(state: QueryState) -> dict[str, Any]:
          if not state["vector_draft"]:
              return {"vector_claims": []}
          decomp = await extract_claims(state["vector_draft"])
          claims = [
              Claim(
                  claim_id=c.claim_id,
                  text=c.text,
                  supporting_evidence_ids=tuple(),
                  nli_label=None,
                  verifier_status=VerifierStatus.NOT_CHECKED
              ) for c in decomp.claims
          ]
          # Extract entities mentioned in claims
          entities = set()
          for c in decomp.claims:
              entities.update(c.entities)
          return {"vector_claims": claims}

      async def graph_claim_extraction_node(state: QueryState) -> dict[str, Any]:
          if not state["graph_draft"]:
              return {"graph_claims": []}
          decomp = await extract_claims(state["graph_draft"])
          claims = [
              Claim(
                  claim_id=c.claim_id,
                  text=c.text,
                  supporting_evidence_ids=tuple(),
                  nli_label=None,
                  verifier_status=VerifierStatus.NOT_CHECKED
              ) for c in decomp.claims
          ]
          return {"graph_claims": claims}

      async def consensus_verifier_node(state: QueryState) -> dict[str, Any]:
          v_claims = list(state["vector_claims"])
          g_claims = list(state["graph_claims"])
          
          # Run live NLI judgments bidirectionally
          for c in v_claims:
              res = await judge_nli(c.text, state["graph_draft"] or "")
              c.nli_label = NLILabel(res.label)
              c.nli_confidence = res.confidence
              c.verifier_status = VerifierStatus.SUPPORTED if res.label == "entailment" else VerifierStatus.UNSUPPORTED
              
          for c in g_claims:
              res = await judge_nli(c.text, state["vector_draft"] or "")
              c.nli_label = NLILabel(res.label)
              c.nli_confidence = res.confidence
              c.verifier_status = VerifierStatus.SUPPORTED if res.label == "entailment" else VerifierStatus.UNSUPPORTED

          # Extract entity sets from claims text
          vector_entities = set()
          for c in v_claims:
              vector_entities.update([w.lower() for w in c.text.split() if len(w) > 4])
          graph_entities = set()
          for c in g_claims:
              graph_entities.update([w.lower() for w in c.text.split() if len(w) > 4])

          report = calculate_consensus(
              trace_id=state["trace_id"],
              vector_claims=tuple(v_claims),
              graph_claims=tuple(g_claims),
              vector_entities=vector_entities,
              graph_entities=graph_entities,
              threshold=state["options"].get("consensus_threshold", 0.7)
          )
          return {"consensus_report": report, "vector_claims": v_claims, "graph_claims": g_claims}

      async def synthesis_node(state: QueryState) -> dict[str, Any]:
          client = get_ollama_client()
          settings = get_settings()
          final_prompt = (
              f"Synthesize a final unified secure response based on two draft reviews:\n"
              f"Vector Draft: {state['vector_draft']}\n"
              f"Graph Draft: {state['graph_draft']}\n"
              f"Agreement: {state['consensus_report'].agreement_score if state['consensus_report'] else 0.0}"
          )
          response = await client.chat(
              model=settings.llm_model,
              messages=[
                  {"role": "system", "content": "You are a synthesis agent. Produce a secure, unified answer grounded strictly in both drafts."},
                  {"role": "user", "content": final_prompt}
              ]
          )
          return {"final_answer": Answer(text=response.message.content or "", citations=tuple())}

      # Add all nodes
      builder.add_node("vector_retrieval", vector_retrieval_node)
      builder.add_node("graph_retrieval", graph_retrieval_node)
      builder.add_node("vector_draft", vector_draft_node)
      builder.add_node("graph_draft", graph_draft_node)
      builder.add_node("vector_claims", vector_claim_extraction_node)
      builder.add_node("graph_claims", graph_claim_extraction_node)
      builder.add_node("consensus_verifier", consensus_verifier_node)
      builder.add_node("synthesis", synthesis_node)

      # Build sequential parallel channels
      builder.add_edge(START, "vector_retrieval")
      builder.add_edge(START, "graph_retrieval")
      builder.add_edge("vector_retrieval", "vector_draft")
      builder.add_edge("graph_retrieval", "graph_draft")
      builder.add_edge("vector_draft", "vector_claims")
      builder.add_edge("graph_draft", "graph_claims")
      builder.add_edge("vector_claims", "consensus_verifier")
      builder.add_edge("graph_claims", "consensus_verifier")
      builder.add_edge("consensus_verifier", "synthesis")
      builder.add_edge("synthesis", END)

      return builder.compile()
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `.venv/bin/pytest tests/integration/test_workflow_live.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/securgraph/orchestration/ tests/integration/test_workflow_live.py
  git commit -m "feat(m4.5): implement LangGraph orchestration workflow state loops"
  ```

---

### Task 5: Implement API `/query` Route integrated with LangGraph

**Files:**
- Create: `src/securgraph/api/routes/query.py`
- Modify: `src/securgraph/api/main.py`
- Create: `tests/integration/test_api_query_live.py`

- [ ] **Step 1: Write the live integration test**
  Create `tests/integration/test_api_query_live.py` verifying `/query` returns the proper `QueryResponse` Pydantic response:
  ```python
  import pytest
  from fastapi.testclient import TestClient
  from securgraph.api.main import app

  client = TestClient(app)

  @pytest.mark.integration
  def test_api_query_endpoint_live() -> None:
      res = client.post(
          "/query",
          json={
              "tenant_id": "mrd",
              "user_id": "user:alice",
              "query": "Is system A secure?",
              "options": {"consensus_threshold": 0.7}
          }
      )
      assert res.status_code == 200
      data = res.json()
      assert "answer" in data
      assert "confidence" in data
      assert "trace_id" in data
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `.venv/bin/pytest tests/integration/test_api_query_live.py -v`
  Expected: FAIL with `404 Not Found` (endpoint `/query` doesn't exist)

- [ ] **Step 3: Write minimal implementation**
  Create `src/securgraph/api/routes/query.py`:
  ```python
  from fastapi import APIRouter, Depends
  from securgraph.domain.query import QueryRequest, QueryResponse, ConfidenceBlock, QueryChannels
  from securgraph.domain.base import Subject
  from securgraph.orchestration.workflow import compile_workflow
  from securgraph.api.dependencies import get_qdrant_client, get_neo4j_driver
  from securgraph.indexing.vector_retriever import QdrantRetriever
  from securgraph.indexing.graph_retriever import AuthorizedGraphRetriever
  from securgraph.adapters.embeddings import BGEM3EmbeddingAdapter
  from securgraph.domain import OpenFGAPolicyAdapter
  import uuid

  router = APIRouter()

  def get_workflow():
      qclient = get_qdrant_client()
      embedder = BGEM3EmbeddingAdapter()
      qdrant_retriever = QdrantRetriever(qclient, embedder)
      
      neo_driver = get_neo4j_driver()
      fga_adapter = OpenFGAPolicyAdapter()
      graph_retriever = AuthorizedGraphRetriever(neo_driver, fga_adapter)

      return compile_workflow(qdrant_retriever, graph_retriever)

  @router.post("/query", response_model=QueryResponse)
  async def query_endpoint(
      req: QueryRequest,
      workflow=Depends(get_workflow)
  ) -> QueryResponse:
      trace_id = f"trace_{uuid.uuid4().hex[:8]}"
      initial_state = {
          "query": req.query,
          "subject": Subject(user_id=req.user_id, tenant_id=req.tenant_id),
          "options": req.options.model_dump(),
          "trace_id": trace_id,
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

      res = await workflow.ainvoke(initial_state)
      ans = res["final_answer"]
      rep = res["consensus_report"]

      return QueryResponse(
          answer=ans.text,
          citations=ans.citations,
          confidence=ConfidenceBlock(
              high_trust=rep.high_trust if rep else False,
              agreement_score=rep.agreement_score if rep else None,
              entity_overlap=rep.entity_overlap_jaccard if rep else None,
              claim_nli_score=rep.claim_nli_score if rep else None,
              evidence_coverage=1.0
          ),
          channels=QueryChannels(),
          trace_id=trace_id,
          warnings=tuple(res["warnings"])
      )
  ```
  Now register this router in `src/securgraph/api/main.py`:
  ```python
  # Add in import block:
  from securgraph.api.routes import query

  # Add inside create_app():
  app.include_router(query.router, tags=["Query"])
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `.venv/bin/pytest tests/integration/test_api_query_live.py -v`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/securgraph/api/routes/query.py src/securgraph/api/main.py tests/integration/test_api_query_live.py
  git commit -m "feat(m4.5): implement POST /query API router integrated with LangGraph workflow"
  ```
