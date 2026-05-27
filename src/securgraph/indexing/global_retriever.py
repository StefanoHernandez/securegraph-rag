"""Global retriever for Hierarchical Graph Communities (Global RAG)."""

from __future__ import annotations

import asyncio
from typing import Any

from neo4j import AsyncDriver
from openfga_sdk.models import CheckRequest, TupleKey

from securgraph.domain.community import Community, CommunitySummaryResponse
from securgraph.domain.policy import OpenFGAPolicyAdapter, PolicyDecision, PolicyDecisionValue, PolicyObjectType, utcnow
from securgraph.logging import get_logger

_LOG = get_logger(__name__)


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
    ) -> list[CommunitySummaryResponse]:
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
        cypher += "RETURN c.community_id AS community_id, c.title AS title, c.summary AS summary, c.level AS level, c.member_entities AS member_entities"

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
            )
            for r in records
        ]

        # 2. Filter communities via OpenFGA ReBAC
        authorized_comms = await self.filter_communities(subject, communities, trace_id or "trace_global")
        
        return [
            CommunitySummaryResponse(
                community_id=c.community_id,
                title=c.title,
                summary=c.summary,
                level=c.level,
                member_entities=list(c.member_entities),
            )
            for c in authorized_comms
        ]

    async def filter_communities(
        self,
        subject: Any,
        communities: list[Community],
        trace_id: str,
    ) -> list[Community]:
        """Verify that the user has permission to read the communities."""
        if not communities:
            return []

        async def check_community(c: Community) -> tuple[Community, bool]:
            # Construct check request: Can user Bob view community c_001?
            tuple_key = TupleKey(  # type: ignore[no-untyped-call]
                user=subject.fga_user,
                relation="viewer",
                object=f"community:{c.community_id}",
            )

            try:
                # Direct FGA client check
                response = await self.fga.client.check(CheckRequest(tuple_key=tuple_key))  # type: ignore[arg-type, no-untyped-call]
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
            except Exception:
                pass

            return c, allowed

        # Execute authorization checks concurrently
        results = await asyncio.gather(*(check_community(c) for c in communities))
        
        return [c for c, allowed in results if allowed]
