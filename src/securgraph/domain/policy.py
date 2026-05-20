"""Policy decisions — the audit primitive for contribution C1."""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import StrEnum

import structlog
from openfga_sdk import OpenFgaClient
from openfga_sdk.models import CheckRequest, TupleKey
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
            tuple_key = TupleKey(  # type: ignore[no-untyped-call]
                user=subject.fga_user,
                relation="viewer",
                object=f"chunk:{chunk.chunk_id}",
            )

            try:
                response = await self.client.check(CheckRequest(tuple_key=tuple_key))  # type: ignore[arg-type, no-untyped-call]
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
            return chunk, decision

        # Run checks concurrently
        results = await asyncio.gather(*(check_chunk(c) for c in chunks))

        authorized_chunks = [
            chunk for chunk, dec in results if dec.decision == PolicyDecisionValue.ALLOW
        ]
        all_decisions = [dec for _, dec in results]

        return authorized_chunks, all_decisions
