"""Policy decisions — the audit primitive for contribution C1."""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import StrEnum

import structlog
from openfga_sdk import OpenFgaClient
from openfga_sdk.client.models import ClientCheckRequest
from pydantic import Field

from securgraph.domain.base import SGModel, Subject, TenantId, TraceId, UserId, utcnow
from securgraph.domain.chunk import Chunk

logger = structlog.get_logger(__name__)


class PolicyObjectType(StrEnum):
    DOCUMENT = "document"
    CHUNK = "chunk"
    GRAPH_NODE = "graph_node"
    GRAPH_EDGE = "graph_edge"
    GRAPH_PATH = "graph_path"
    COMMUNITY = "community"


class PolicyAction(StrEnum):
    READ = "read"
    EXPLAIN = "explain"
    AUDIT = "audit"
    ADMINISTER = "administer"


class PolicyDecisionValue(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class PolicyDecision(SGModel):
    """A single authorization check, recorded for verifiable access correctness.

    Persisted to Postgres. Together with `RetrievalEvidence.policy_check_id`,
    these decisions let an auditor re-derive that every piece of evidence used
    was authorized for the requesting subject at request time.
    """

    decision_id: str = Field(min_length=1, max_length=128)
    trace_id: TraceId
    tenant_id: TenantId
    user_id: UserId
    object_type: PolicyObjectType
    object_id: str = Field(min_length=1, max_length=256)
    action: PolicyAction = PolicyAction.READ
    decision: PolicyDecisionValue
    reason_code: str | None = Field(
        default=None,
        description="Stable machine-readable reason (e.g. 'project_membership', 'cross_tenant').",
    )
    policy_engine: str = Field(
        default="openfga",
        description="Which engine produced the decision. Stable across versions.",
    )
    evaluated_at: datetime = Field(default_factory=utcnow)
    retriever_id: str | None = Field(
        default=None,
        description="Retriever that surfaced the denied candidate (audit v2).",
    )
    resource_hash: str | None = Field(
        default=None,
        description="Content hash of the denied resource; no restricted text stored.",
    )


class OpenFGAPolicyAdapter:
    """Relationship-Based Access Control (ReBAC) policy enforcer using OpenFGA.

    Ensures secure data access across dense/lexical search results and graph components,
    guaranteeing multi-tenant isolation and strict fail-closed operations.
    """

    def __init__(self, fga_client: OpenFgaClient) -> None:
        self.client = fga_client

    async def filter_chunks(
        self, subject: Subject, chunks: list[Chunk], trace_id: str
    ) -> tuple[list[Chunk], list[PolicyDecision]]:
        """Run batch authorization checks on chunks. Returns authorized chunks & decisions."""
        if not chunks:
            return [], []

        async def check_chunk(chunk: Chunk) -> tuple[Chunk, PolicyDecision]:
            # Construct check request: Can user Bob view chunk c_001?
            req = ClientCheckRequest(
                user=subject.fga_user,
                relation="viewer",
                object=f"chunk:{chunk.chunk_id}",
            )

            try:
                response = await self.client.check(req)
                allowed = response.allowed or False
            except Exception as e:
                logger.error(
                    "OpenFGA check failed, failing closed",
                    error=str(e),
                    chunk_id=chunk.chunk_id,
                    user=subject.fga_user,
                )
                allowed = False  # Fail closed

            if not allowed:
                logger.warning(
                    "Access denied to chunk",
                    chunk_id=chunk.chunk_id,
                    user=subject.fga_user,
                    trace_id=trace_id,
                )

            decision = PolicyDecision(
                decision_id=f"dec_{chunk.chunk_id}_{utcnow().timestamp()}",
                trace_id=trace_id,
                tenant_id=subject.tenant_id,
                user_id=subject.user_id,
                object_type=PolicyObjectType.CHUNK,
                object_id=chunk.chunk_id,
                decision=PolicyDecisionValue.ALLOW if allowed else PolicyDecisionValue.DENY,
                reason_code="openfga_ok" if allowed else "unauthorized_rbac",
            )

            try:
                from securgraph.observability.otel import POLICY_DECISION_COUNT

                POLICY_DECISION_COUNT.labels(
                    tenant_id=subject.tenant_id,
                    object_type=PolicyObjectType.CHUNK.value,
                    decision=decision.decision.value,
                ).inc()
            except Exception:  # noqa: S110 — metrics emission is best-effort
                pass

            return chunk, decision

        # Run checks concurrently
        results = await asyncio.gather(*(check_chunk(c) for c in chunks))

        authorized_chunks = [
            chunk for chunk, dec in results if dec.decision == PolicyDecisionValue.ALLOW
        ]
        all_decisions = [dec for _, dec in results]

        return authorized_chunks, all_decisions

    async def filter_chunk_ids(
        self, subject: Subject, chunk_ids: list[str], trace_id: str
    ) -> tuple[set[str], list[PolicyDecision]]:
        """ID-only variant of :meth:`filter_chunks` for callers that only have ids.

        Used by the vector retrieval path where retrieval emits ``RetrievalEvidence``
        keyed on ``object_id`` rather than full ``Chunk`` objects. Same fail-closed
        semantics: any FGA error or denial drops the id from the authorized set.
        """
        if not chunk_ids:
            return set(), []

        # De-duplicate while preserving order so the metric counter doesn't
        # double-charge a chunk that surfaced from both dense and lexical lists.
        unique_ids: list[str] = []
        seen: set[str] = set()
        for cid in chunk_ids:
            if cid not in seen:
                unique_ids.append(cid)
                seen.add(cid)

        async def check_id(chunk_id: str) -> tuple[str, PolicyDecision]:
            req = ClientCheckRequest(
                user=subject.fga_user,
                relation="viewer",
                object=f"chunk:{chunk_id}",
            )
            try:
                response = await self.client.check(req)
                allowed = response.allowed or False
            except Exception as e:
                logger.error(
                    "OpenFGA check failed, failing closed",
                    error=str(e),
                    chunk_id=chunk_id,
                    user=subject.fga_user,
                )
                allowed = False

            if not allowed:
                logger.warning(
                    "Access denied to chunk",
                    chunk_id=chunk_id,
                    user=subject.fga_user,
                    trace_id=trace_id,
                )

            decision = PolicyDecision(
                decision_id=f"dec_{chunk_id}_{utcnow().timestamp()}",
                trace_id=trace_id,
                tenant_id=subject.tenant_id,
                user_id=subject.user_id,
                object_type=PolicyObjectType.CHUNK,
                object_id=chunk_id,
                decision=PolicyDecisionValue.ALLOW if allowed else PolicyDecisionValue.DENY,
                reason_code="openfga_ok" if allowed else "unauthorized_rbac",
            )

            try:
                from securgraph.observability.otel import POLICY_DECISION_COUNT

                POLICY_DECISION_COUNT.labels(
                    tenant_id=subject.tenant_id,
                    object_type=PolicyObjectType.CHUNK.value,
                    decision=decision.decision.value,
                ).inc()
            except Exception:  # noqa: S110 — metrics emission is best-effort
                pass

            return chunk_id, decision

        results = await asyncio.gather(*(check_id(cid) for cid in unique_ids))

        authorized = {cid for cid, dec in results if dec.decision == PolicyDecisionValue.ALLOW}
        all_decisions = [dec for _, dec in results]
        return authorized, all_decisions
