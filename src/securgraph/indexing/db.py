"""SQLAlchemy 2 ORM models for relational state.

Tables landed in M1:
    - ``documents``               (one row per ingested source)
    - ``chunks``                  (retrievable units; FK → documents)
    - ``ingestion_jobs``          (job-state machine)
    - ``ingestion_provenance``    (immutable per-job audit row)

Tables added in later milestones (forward-declared so they live alongside):
    - ``policy_decisions``        (M4)
    - ``provenance_traces``       (M4.5/M6)
    - ``agent_runs``              (M4.5)
    - ``consensus_reports``       (M4.5)
    - ``audit_events``            (M7)

Schemas use PostgreSQL ``JSONB`` for tuple/list/dict-valued columns. All
timestamps are ``TIMESTAMPTZ`` and converted to timezone-aware Python
datetimes by SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    # Prefer JSONB on Postgres, fall back to JSON elsewhere (tests using SQLite
    # need this so the same models load).
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONB().with_variant(JSON(), "sqlite"),
    }


class DocumentRow(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False, default="other")
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default="internal")
    owner_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    language: Mapped[str] = mapped_column(String(12), nullable=False, default="en")
    policy_tags: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunks: Mapped[list[ChunkRow]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Idempotency check: same content + same uri + same tenant ⇒ existing doc.
        UniqueConstraint("tenant_id", "source_uri", "source_hash", name="uq_doc_idempotent"),
        Index("ix_documents_tenant_classification", "tenant_id", "classification"),
    )


class ChunkRow(Base):
    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_chunk_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_chunk_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    classification: Mapped[str] = mapped_column(String(32), nullable=False, default="internal")
    language: Mapped[str] = mapped_column(String(12), nullable=False, default="en")
    policy_tags: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )

    document: Mapped[DocumentRow] = relationship(back_populates="chunks")

    __table_args__ = (Index("ix_chunks_doc_ordinal", "document_id", "ordinal"),)


class PolicyDecisionRow(Base):
    __tablename__ = "policy_decisions"

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(256), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False, default="read")
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(256), nullable=True)
    policy_engine: Mapped[str] = mapped_column(String(64), nullable=False, default="openfga")
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tamper_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class IngestionJobRow(Base):
    __tablename__ = "ingestion_jobs"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestionProvenanceRow(Base):
    __tablename__ = "ingestion_provenance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("ingestion_jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(32), nullable=False)
    embedder_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    chunks_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")


class QueryTraceRow(Base):
    __tablename__ = "query_traces"

    trace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tamper_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Consensus snapshot (C2) — recorded so the verdict is auditable from the
    # trace alone. Deliberately NOT part of the tamper-hash payload (see
    # tamper.chain_query_trace / verify_table_chain field lists), so adding
    # these columns does not invalidate existing chained rows.
    agreement_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_trust: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    entity_overlap: Mapped[float | None] = mapped_column(Float, nullable=True)
    claim_nli_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    evidence: Mapped[list[RetrievalEvidenceRow]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )


class RetrievalEvidenceRow(Base):
    __tablename__ = "retrieval_evidence"

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("query_traces.trace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_id: Mapped[str] = mapped_column(String(256), nullable=False)
    retriever_id: Mapped[str] = mapped_column(String(64), nullable=False)  # "vector", "lexical", "graph"
    original_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    original_score: Mapped[float] = mapped_column(Float, nullable=False)
    included_in_prompt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tamper_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    trace: Mapped[QueryTraceRow] = relationship(back_populates="evidence")


class AuthUserRow(Base):
    __tablename__ = "auth_users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="viewer")

