"""Global retriever for Hierarchical Graph Communities (Global RAG)."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, cast

from neo4j import AsyncDriver
from openfga_sdk.client.models import ClientCheckRequest

from securgraph.domain.base import utcnow
from securgraph.domain.community import Community, CommunitySummaryResponse
from securgraph.domain.policy import (
    OpenFGAPolicyAdapter,
    PolicyDecision,
    PolicyDecisionValue,
    PolicyObjectType,
)
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


@dataclass(frozen=True)
class CommunityRetrievalResult:
    """Authorized community summaries plus the complete candidate audit trail."""

    communities: list[CommunitySummaryResponse]
    decisions: list[PolicyDecision]


def _community_digest(community: Community) -> str:
    return "sha256:" + hashlib.sha256(community.summary.encode("utf-8")).hexdigest()


def _community_decision(
    community: Community,
    *,
    subject: Any,
    trace_id: str,
    allowed: bool,
    reason_code: str,
) -> PolicyDecision:
    return PolicyDecision(
        decision_id=f"dec_community_{community.community_id}_{utcnow().timestamp()}",
        trace_id=trace_id,
        tenant_id=subject.tenant_id,
        user_id=subject.user_id,
        object_type=PolicyObjectType.COMMUNITY,
        object_id=community.community_id,
        decision=PolicyDecisionValue.ALLOW if allowed else PolicyDecisionValue.DENY,
        reason_code=reason_code,
        retriever_id="community",
        resource_hash=_community_digest(community),
    )


class AuthorizedCommunityRetriever:
    """Retrieve and filter hierarchical communities from Neo4j using ReBAC authorization."""

    def __init__(self, driver: AsyncDriver, fga: OpenFGAPolicyAdapter) -> None:
        self.driver = driver
        self.fga = fga

    async def retrieve_communities(
        self,
        subject: Any,
        level: int | None = None,
        trace_id: str | None = None,
        return_decisions: bool = False,
    ) -> list[CommunitySummaryResponse] | CommunityRetrievalResult:
        """Fetch communities filtered by tenant_id and authorized by ReBAC."""
        _LOG.info(
            "retrieve_communities_started",
            tenant_id=subject.tenant_id,
            level=level,
            trace_id=trace_id,
        )

        # 1. Fetch communities matching the tenant and level from Neo4j
        cypher = "MATCH (c:Community {tenant_id: $tenant_id}) "
        if level is not None:
            cypher += "WHERE c.level = $level "
        cypher += (
            "RETURN c.community_id AS community_id, c.title AS title, "
            "c.summary AS summary, c.level AS level, c.member_entities AS member_entities, "
            "c.policy_class_key AS policy_class_key"
        )

        async with self.driver.session() as session:
            result = await session.run(cypher, tenant_id=subject.tenant_id, level=level)
            records = await result.data()

        communities = [
            Community(
                community_id=r["community_id"],
                tenant_id=subject.tenant_id,
                title=r["title"],
                summary=r["summary"],
                level=r["level"],
                member_entities=tuple(r["member_entities"] or []),
                policy_class_key=r.get("policy_class_key") or "unlabeled",
            )
            for r in records
        ]

        # 2. Per-class authorization gate (one FGA check per policy-equivalence-class)
        class_keys = {c.policy_class_key for c in communities}
        allowed_keys = await self.authorized_classes(subject, class_keys)
        in_class = [c for c in communities if c.policy_class_key in allowed_keys]
        trace = trace_id or "trace_global"
        decisions = [
            _community_decision(
                community,
                subject=subject,
                trace_id=trace,
                allowed=False,
                reason_code="community_policy_class_denied",
            )
            for community in communities
            if community.policy_class_key not in allowed_keys
        ]

        # 3. Defense-in-depth: per-community ReBAC check
        filtered = await self.filter_communities(
            subject, in_class, trace, return_decisions=True
        )
        authorized_comms, direct_decisions = cast(
            tuple[list[Community], list[PolicyDecision]], filtered
        )
        decisions.extend(direct_decisions)
        direct_decision_by_id = {
            decision.object_id: decision
            for decision in direct_decisions
            if decision.decision == PolicyDecisionValue.ALLOW
        }
        missing_decisions = {
            community.community_id
            for community in authorized_comms
            if community.community_id not in direct_decision_by_id
        }
        if missing_decisions:
            raise RuntimeError(
                "Authorized communities are missing auditable decisions: "
                + ", ".join(sorted(missing_decisions))
            )

        summaries = [
            CommunitySummaryResponse(
                community_id=c.community_id,
                title=c.title,
                summary=c.summary,
                level=c.level,
                member_entities=list(c.member_entities),
                policy_class_key=c.policy_class_key,
                policy_check_id=direct_decision_by_id[c.community_id].decision_id,
            )
            for c in authorized_comms
        ]
        if return_decisions:
            return CommunityRetrievalResult(communities=summaries, decisions=decisions)
        return summaries

    async def authorized_classes(self, subject: Any, class_keys: set[str]) -> set[str]:
        """Return the subset of class keys the subject may view (one check each)."""

        async def check(key: str) -> tuple[str, bool]:
            req = ClientCheckRequest(
                user=subject.fga_user,
                relation="viewer",
                object=f"policy_class:{key}",
            )
            try:
                resp = await self.fga.client.check(req)
                return key, bool(resp.allowed)
            except Exception as e:
                _LOG.warning(
                    "policy_class_check_failed_failing_closed",
                    error=str(e),
                    class_key=key,
                )
                return key, False

        if not class_keys:
            return set()
        results = await asyncio.gather(*(check(k) for k in class_keys))
        return {k for k, ok in results if ok}

    async def filter_communities(
        self,
        subject: Any,
        communities: list[Community],
        trace_id: str,
        return_decisions: bool = False,
    ) -> list[Community] | tuple[list[Community], list[PolicyDecision]]:
        """Verify that the user has permission to read the communities."""
        if not communities:
            return ([], []) if return_decisions else []

        async def check_community(c: Community) -> tuple[Community, bool]:
            # Construct check request: Can user Bob view community c_001?
            req = ClientCheckRequest(
                user=subject.fga_user,
                relation="viewer",
                object=f"community:{c.community_id}",
            )

            try:
                # Direct FGA client check
                response = await self.fga.client.check(req)
                allowed = response.allowed or False
            except Exception as e:
                _LOG.warning(
                    "openfga_check_failed_failing_closed",
                    error=str(e),
                    community_id=c.community_id,
                )
                allowed = False  # Fail closed

            # Record metrics and decisions
            try:
                from securgraph.observability.otel import POLICY_DECISION_COUNT

                POLICY_DECISION_COUNT.labels(
                    tenant_id=subject.tenant_id,
                    object_type="community",
                    decision="allow" if allowed else "deny",
                ).inc()
            except Exception:  # noqa: S110
                pass

            return c, allowed

        # Execute authorization checks concurrently
        results = await asyncio.gather(*(check_community(c) for c in communities))
        authorized = [community for community, allowed in results if allowed]
        if not return_decisions:
            return authorized
        decisions = [
            _community_decision(
                community,
                subject=subject,
                trace_id=trace_id,
                allowed=allowed,
                reason_code="community_openfga_ok" if allowed else "community_openfga_denied",
            )
            for community, allowed in results
        ]
        return authorized, decisions
