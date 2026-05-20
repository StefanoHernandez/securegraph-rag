# Milestone 6: Provenance & Explainability Spec

This milestone covers recording every retrieval activity, policy decision, and generation step into a SQL-backed trace store aligned with the W3C PROV-O standard. It implements the `/explain/{trace_id}` endpoint to provide verifiable, audit-ready lineage.

---

## 1. W3C PROV-O Lineage Alignment

Every query response is backed by a provenance graph:
- **Agents**: The user initiating the query (`prov:Person`), the system service (`prov:SoftwareAgent`).
- **Activities**: Retrieval (`search`), Authorization check (`fga_check`), Generation (`llm_generate`).
- **Entities**: Source document (`prov:Entity`), Text chunk, Graph relationships, Output answer.

```
[User / Agent] ---> (Retrieves Chunks / Activity) ---> [Chunks / Entity]
                                                             |
                                                       (FGA Check)
                                                             |
                                                             v
[Generated Answer] <--- (Generates / Activity) <--- [Authorized Context]
```

---

## 2. PostgreSQL Tracing Schemas

We will define relational tables inside `src/securgraph/indexing/db.py` to store complete query traces.

### 📝 SQL Schema Definitions

```python
from __future__ import annotations

from sqlalchemy import String, Float, Boolean, ForeignKey, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from securgraph.indexing.db import Base, utcnow

class QueryTraceRow(Base):
    __tablename__ = "query_traces"

    trace_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    query: Mapped[str] = mapped_column(String, nullable=False)
    answer: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

class PolicyDecisionRow(Base):
    __tablename__ = "policy_decisions"

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), ForeignKey("query_traces.trace_id"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "chunk" or "node"
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # "allow" or "deny"
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    checked_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), default=utcnow)

class RetrievalEvidenceRow(Base):
    __tablename__ = "retrieval_evidence"

    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), ForeignKey("query_traces.trace_id"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    retriever_id: Mapped[str] = mapped_column(String(32), nullable=False)  # "vector", "lexical", "graph"
    original_rank: Mapped[int] = mapped_column(nullable=False)
    original_score: Mapped[float] = mapped_column(Float, nullable=False)
    included_in_prompt: Mapped[bool] = mapped_column(Boolean, default=False)
```

---

## 3. The `/explain/{trace_id}` API Endpoint

The explainability endpoint compiles the recorded provenance trace into an explainability report.

### 📝 FastAPI Router (`src/securgraph/api/routes/explain.py`)

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from securgraph.api.dependencies import get_db_session
from securgraph.domain.base import SGModel

router = APIRouter(tags=["explainability"])

class ExplainReport(SGModel):
    trace_id: str
    query: str
    answer: str
    evidence: list[dict]
    policy_summary: dict
    verdict: str

@router.get(
    "/explain/{trace_id}",
    response_model=ExplainReport,
    summary="Get explainability report for a query",
)
async def explain_query(
    trace_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> ExplainReport:
    """Compile W3C-aligned provenance report for a past RAG trace."""
    # 1. Fetch trace row
    trace = await session.get(QueryTraceRow, trace_id)
    if not trace:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace {trace_id} not found."
        )
        
    # 2. Fetch associated evidence & decisions
    # (Implementation fetches from database tables...)
    
    # 3. Secure output: Hide actual text / data of denied elements from non-auditors
    # Only return decision ID, object ID, and status code.
    
    return ExplainReport(
        trace_id=trace_id,
        query=trace.query,
        answer=trace.answer or "Pending",
        evidence=[...],
        policy_summary={"allowed": ..., "denied": ...},
        verdict="verifiable"
    )
```
