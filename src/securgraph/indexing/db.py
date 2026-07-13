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
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
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
    chain_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tamper_version: Mapped[str] = mapped_column(
        String(8), nullable=False, default="v2", server_default="v2"
    )
    retriever_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "chain_sequence", name="uq_policy_chain_sequence"),
    )


class IngestionJobRow(Base):
    __tablename__ = "ingestion_jobs"

    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    current_step: Mapped[str] = mapped_column(String(64), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class IngestionSourceRow(Base):
    """Encrypted original and immutable request metadata for worker retries."""

    __tablename__ = "ingestion_sources"

    job_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("ingestion_jobs.job_id", ondelete="CASCADE"), primary_key=True
    )
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_tags: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    direct_viewers: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    title_override: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IngestionSagaStepRow(Base):
    """Durable ledger of external ingestion effects and their compensation."""

    __tablename__ = "ingestion_saga_steps"

    job_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("ingestion_jobs.job_id", ondelete="CASCADE"), primary_key=True
    )
    step: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_ids: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    compensation_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


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
    chain_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tamper_version: Mapped[str] = mapped_column(
        String(8), nullable=False, default="v2", server_default="v2"
    )

    # v1 chains excluded this snapshot. v2 chains seal it with the trace payload.
    agreement_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    high_trust: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    entity_overlap: Mapped[float | None] = mapped_column(Float, nullable=True)
    claim_nli_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    consensus_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    consensus_verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    judge_model: Mapped[str | None] = mapped_column(String(256), nullable=True)

    evidence: Mapped[list[RetrievalEvidenceRow]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "chain_sequence", name="uq_trace_chain_sequence"),
    )


class RetrievalEvidenceRow(Base):
    __tablename__ = "retrieval_evidence"

    evidence_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    trace_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("query_traces.trace_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_id: Mapped[str] = mapped_column(String(256), nullable=False)
    retriever_id: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # "vector", "lexical", "graph"
    original_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    original_score: Mapped[float] = mapped_column(Float, nullable=False)
    included_in_prompt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tamper_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chain_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tamper_version: Mapped[str] = mapped_column(
        String(8), nullable=False, default="v2", server_default="v2"
    )

    trace: Mapped[QueryTraceRow] = relationship(back_populates="evidence")

    __table_args__ = (
        UniqueConstraint("tenant_id", "chain_sequence", name="uq_evidence_chain_sequence"),
    )


class AuthUserRow(Base):
    __tablename__ = "auth_users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(320), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list[TenantMembershipRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[AuthSessionRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class TenantRow(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    tenant_admin_settings_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    memberships: Mapped[list[TenantMembershipRow]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended', 'archived')", name="ck_tenant_status"),
    )


class TenantMembershipRow(Base):
    __tablename__ = "tenant_memberships"

    tenant_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    tenant: Mapped[TenantRow] = relationship(back_populates="memberships")
    user: Mapped[AuthUserRow] = relationship(back_populates="memberships")

    __table_args__ = (
        CheckConstraint(
            "role IN ('super_admin', 'tenant_admin', 'analyst', 'viewer')",
            name="ck_membership_role",
        ),
        Index("ix_membership_user_active", "user_id", "is_active"),
    )


class AuthSessionRow(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[AuthUserRow] = relationship(back_populates="sessions")


class GroupRow(Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="functional")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_group_tenant_slug"),
        CheckConstraint("kind IN ('functional', 'project')", name="ck_group_kind"),
    )


class GroupMembershipRow(Base):
    __tablename__ = "group_memberships"

    group_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("auth_users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccessTemplateRow(Base):
    __tablename__ = "document_access_templates"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    default_classification: Mapped[str] = mapped_column(
        String(32), nullable=False, default="internal"
    )
    owner_rule: Mapped[str] = mapped_column(String(32), nullable=False, default="uploader")
    allowed_group_ids: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    direct_viewer_ids: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "document_type", name="uq_access_template_type"),
        CheckConstraint(
            "default_classification IN ('public', 'internal', 'confidential', 'restricted')",
            name="ck_access_template_classification",
        ),
        CheckConstraint(
            "owner_rule IN ('uploader', 'tenant_admin')", name="ck_access_template_owner_rule"
        ),
    )


class AdminAuditEventRow(Base):
    __tablename__ = "admin_audit_events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    actor_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(256), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    event_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderCredentialRow(Base):
    __tablename__ = "provider_credentials"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configured")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_provider_credential_scope"),
    )


class RuntimeSettingRow(Base):
    __tablename__ = "runtime_settings"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    restart_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_runtime_setting_scope"),)


class ModelCatalogRow(Base):
    __tablename__ = "model_catalog"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "model_id", name="uq_model_catalog_scope"),
    )


class OperationJobRow(Base):
    __tablename__ = "operation_jobs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditChainHeadRow(Base):
    __tablename__ = "audit_chain_heads"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    chain_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuditAnchorRow(Base):
    __tablename__ = "audit_anchors"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    from_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    to_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    merkle_root: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    object_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    anchored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
