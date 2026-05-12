"""Provenance trace, agent runs, ingestion provenance.

These are the persisted records that make contribution C1 (Verifiable Access
Correctness) auditable. Together with `PolicyDecision` and `RetrievalEvidence`
they let an auditor re-derive — without re-running the LLM — that every piece
of evidence used was authorized at request time.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from securgraph.domain.base import SGModel, TenantId, TraceId, UserId, utcnow


class TraceStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class ProvenanceTrace(SGModel):
    """One record per query, with all FKs to the supporting evidence and decisions."""

    trace_id: TraceId
    tenant_id: TenantId
    user_id: UserId
    query: str = Field(min_length=1)
    workflow: str = Field(
        min_length=1,
        max_length=64,
        description="LangGraph workflow label, e.g. 'simple_qa', 'graph_qa', 'compliance_qa'.",
    )
    classification_label: str | None = None
    model: str | None = None
    embedding_model: str | None = None
    prompt_template_version: str | None = None
    evidence_ids: tuple[str, ...] = Field(default_factory=tuple)
    policy_decision_ids: tuple[str, ...] = Field(default_factory=tuple)
    consensus_report_id: str | None = None
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    prompt_token_count: int | None = Field(default=None, ge=0)
    generated_token_count: int | None = Field(default=None, ge=0)
    status: TraceStatus = TraceStatus.STARTED
    error: str | None = None


class AgentRun(SGModel):
    """One execution of a LangGraph node (a "step" in blueprint language)."""

    agent_run_id: str = Field(min_length=1, max_length=128)
    trace_id: TraceId
    agent_name: str = Field(min_length=1, max_length=64)
    input_summary: str | None = Field(default=None, max_length=2048)
    tool_calls: tuple[str, ...] = Field(default_factory=tuple)
    output_summary: str | None = Field(default=None, max_length=2048)
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    status: TraceStatus = TraceStatus.COMPLETED
    error: str | None = None


class IngestionTrace(SGModel):
    """Persisted record of one ingestion job for an individual document.

    Distinct from `ProvenanceTrace` (which is per query). Recorded by M1.
    """

    job_id: str = Field(min_length=1, max_length=128)
    trace_id: TraceId
    tenant_id: TenantId
    document_id: str = Field(min_length=1, max_length=128)
    parser_version: str
    chunker_version: str
    embedder_version: str | None = None
    extractor_version: str | None = None
    source_hash: str
    chunks_created: int = Field(ge=0)
    errors: tuple[str, ...] = Field(default_factory=tuple)
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    status: TraceStatus = TraceStatus.STARTED
