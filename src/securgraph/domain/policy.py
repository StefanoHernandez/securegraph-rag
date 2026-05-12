"""Policy decisions — the audit primitive for contribution C1."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from securgraph.domain.base import SGModel, TenantId, TraceId, UserId, utcnow


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
