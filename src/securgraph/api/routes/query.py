"""FastAPI route for executing secure graph-RAG queries.

Allows querying the secure hybrid Graph-RAG system with unified consensus and ReBAC enforcement.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from opentelemetry import trace
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session, get_graph_retriever, get_qdrant_retriever
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.config import get_settings
from securgraph.domain import (
    ChannelSummary,
    ConfidenceBlock,
    QueryChannels,
    QueryRequest,
    QueryResponse,
    Subject,
    classify_query,
    utcnow,
)
from securgraph.indexing.db import QueryTraceRow, RetrievalEvidenceRow
from securgraph.indexing.tamper import chain_evidence_batch, chain_query_trace
from securgraph.logging import get_logger
from securgraph.observability import get_tracer
from securgraph.observability.otel import (
    CONSENSUS_AGREEMENT,
    QUERY_COUNT,
    QUERY_LATENCY_SECS,
)
from securgraph.orchestration.workflow import compile_workflow

router = APIRouter(tags=["query"])
logger = get_logger(__name__)


@router.post(
    "/query",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute a secure hybrid Graph-RAG query",
)
async def query_pipeline(
    request: QueryRequest,
    qdrant: Annotated[Any, Depends(get_qdrant_retriever)],
    graph: Annotated[Any, Depends(get_graph_retriever)],
    session: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedPrincipal | None = Depends(require_user),
) -> QueryResponse:
    """Run the 8-node LangGraph workflow with consensus verification and policy pruning."""
    if current_user is not None and not current_user.can_access_tenant(request.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token tenant does not match the requested tenant.",
        )
    # SGH-01: effective user id is always derived from the authenticated
    # principal. When auth is disabled (dev/test only), use a fixed anonymous
    # subject so a caller cannot impersonate another user via the request body.
    #
    # Eval authorization simulation: when SECURGRAPH_EVAL_IDENTITY_MODE=true
    # AND auth is disabled AND env is dev/test, allow the caller-provided
    # user_id as the FGA subject. This is NOT impersonation (no mutations are
    # performed as that user) — it is a read-only authorization simulation
    # for ReBAC evaluation. The flag must never be set in production.
    settings = get_settings()
    eval_identity = (
        settings.env.lower() in {"dev", "test"}
        and not current_user
        and os.environ.get("SECURGRAPH_EVAL_IDENTITY_MODE", "").lower()
        in {"1", "true", "yes"}
    )
    if eval_identity:
        logger.warning(
            "eval_identity_simulation_active",
            tenant_id=request.tenant_id,
            simulated_user_id=request.user_id,
        )
        effective_user_id = request.user_id
    else:
        effective_user_id = (
            f"user:{current_user.user_id}" if current_user is not None else "user:anonymous"
        )
    trace_id = str(uuid.uuid4())
    start_time = time.perf_counter()
    tracer = get_tracer()

    with tracer.start_as_current_span("query_pipeline") as span:
        span.set_attribute("securgraph.trace_id", trace_id)
        span.set_attribute("securgraph.tenant_id", request.tenant_id)
        span.set_attribute("securgraph.user_id", effective_user_id)
        span.set_attribute("securgraph.query", request.query)

        subject = Subject(
            tenant_id=request.tenant_id,
            user_id=effective_user_id,
        )

        # 1. Build the initial trace row — do NOT insert yet.
        # It will be inserted after the workflow completes so the hash is computed
        # on the final, complete row (answer + latency_ms + completed_at populated).
        # started_at is set explicitly here (not via server_default) so the
        # value is present in the Python object when chain_query_trace hashes
        # the row. Otherwise the writer hashes started_at=None while the
        # verifier reads the server-default datetime back from the DB, and the
        # chain reports every row as tampered.
        trace_row = QueryTraceRow(
            trace_id=trace_id,
            tenant_id=request.tenant_id,
            user_id=effective_user_id,
            query=request.query,
            started_at=utcnow(),
        )

        # Pre-classify query intent
        classification = classify_query(request.query)
        span.set_attribute("securgraph.classification", classification.value)

        # Prepare initial state for the LangGraph workflow
        state_input = {
            "query": request.query,
            "subject": subject,
            "options": request.options.model_dump(),
            "trace_id": trace_id,
            "vector_context": [],
            "vector_draft": None,
            "vector_claims": [],
            "graph_context": [],
            "graph_draft": None,
            "graph_claims": [],
            "consensus_report": None,
            "final_answer": None,
            "warnings": [],
        }

        try:
            # Compile and execute the workflow
            workflow = compile_workflow(qdrant, graph)
            result = await workflow.ainvoke(state_input)

            final_answer = result.get("final_answer")
            if not final_answer:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Workflow completed but failed to synthesize a final answer.",
                )

            consensus = result.get("consensus_report")

            # Build confidence block
            agreement_score = consensus.agreement_score if consensus else None
            entity_overlap = consensus.entity_overlap_jaccard if consensus else None

            # Drive the "high-trust" UX signal
            high_trust = False
            if consensus is not None:
                threshold = request.options.consensus_threshold
                high_trust = agreement_score is not None and agreement_score >= threshold

            confidence = ConfidenceBlock(
                high_trust=high_trust,
                agreement_score=agreement_score,
                entity_overlap=entity_overlap,
                claim_nli_score=None,
                evidence_coverage=None,
            )

            # Calculate channel summary diagnostics
            vector_context = result.get("vector_context", [])
            vector_count = sum(
                1 for ev in vector_context if getattr(ev, "retriever_id", None) == "vector"
            )
            lexical_count = sum(
                1 for ev in vector_context if getattr(ev, "retriever_id", None) == "lexical"
            )

            graph_context = result.get("graph_context", [])
            graph_count = len(graph_context)

            # Build QueryChannels
            channels = QueryChannels(
                vector=ChannelSummary(
                    evidence_count=vector_count,
                    draft_summary=result.get("vector_draft"),
                    paths=(),
                ),
                lexical=ChannelSummary(
                    evidence_count=lexical_count,
                    draft_summary=None,
                    paths=(),
                ),
                graph=ChannelSummary(
                    evidence_count=graph_count,
                    draft_summary=result.get("graph_draft"),
                    paths=tuple(str(path) for path in graph_context),
                ),
                community=None,
            )

            warnings_list = list(result.get("warnings", []))

            # 2. Persist completed trace and retrieval evidence — single commit.
            # chain_query_trace is called here (once) when all fields are set.
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            trace_row.answer = final_answer.text
            trace_row.latency_ms = latency_ms
            trace_row.completed_at = utcnow()
            # Snapshot the consensus verdict (C2) so /explain can audit it from
            # the trace. Not part of the tamper-hash payload.
            trace_row.agreement_score = agreement_score
            trace_row.high_trust = high_trust
            trace_row.entity_overlap = entity_overlap
            trace_row.claim_nli_score = confidence.claim_nli_score

            evidence_rows = []
            for ev in vector_context:
                evidence_rows.append(
                    RetrievalEvidenceRow(
                        evidence_id=ev.evidence_id,
                        trace_id=trace_id,
                        tenant_id=ev.tenant_id,
                        object_id=ev.object_id,
                        retriever_id=ev.retriever_id.value,
                        original_rank=ev.original_rank,
                        original_score=ev.original_score,
                        included_in_prompt=ev.included_in_prompt,
                        tamper_hash=None,
                    )
                )

            for rank, path in enumerate(graph_context):
                evidence_rows.append(
                    RetrievalEvidenceRow(
                        evidence_id=f"ev_graph_{path.path_id}_{trace_id}",
                        trace_id=trace_id,
                        tenant_id=request.tenant_id,
                        object_id=path.path_id,
                        retriever_id="graph",
                        original_rank=rank,
                        original_score=path.confidence,
                        included_in_prompt=True,
                        tamper_hash=None,
                    )
                )

            if settings.audit_tamper_evident:
                # chain_query_trace runs once here after all fields are populated.
                await chain_query_trace(session, trace_row)
                if evidence_rows:
                    await chain_evidence_batch(session, evidence_rows)

            # Insert the trace row and all evidence in a single transaction.
            session.add(trace_row)
            if evidence_rows:
                session.add_all(evidence_rows)

            await session.commit()

            # Record Prometheus Metrics & OTel Attributes on success
            QUERY_COUNT.labels(tenant_id=request.tenant_id, status="success").inc()
            QUERY_LATENCY_SECS.labels(tenant_id=request.tenant_id).observe(latency_ms / 1000.0)
            if agreement_score is not None:
                CONSENSUS_AGREEMENT.labels(tenant_id=request.tenant_id).observe(agreement_score)
                span.set_attribute("securgraph.agreement_score", agreement_score)
                span.set_attribute("securgraph.high_trust", high_trust)

            span.set_attribute("securgraph.vector_count", vector_count)
            span.set_attribute("securgraph.lexical_count", lexical_count)
            span.set_attribute("securgraph.graph_count", graph_count)

            return QueryResponse(
                answer=final_answer.text,
                citations=final_answer.citations,
                confidence=confidence,
                channels=channels,
                trace_id=trace_id,
                warnings=tuple(warnings_list),
            )

        except HTTPException as he:
            # Increment failure metrics and set span error status
            QUERY_COUNT.labels(tenant_id=request.tenant_id, status="failure").inc()
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            QUERY_LATENCY_SECS.labels(tenant_id=request.tenant_id).observe(latency_ms / 1000.0)
            span.record_exception(he)
            span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, str(he)))

            try:
                await session.rollback()
                from sqlalchemy import update

                await session.execute(
                    update(QueryTraceRow)
                    .where(QueryTraceRow.trace_id == trace_id)
                    .values(completed_at=func.now(), latency_ms=latency_ms)
                )
                await session.commit()
            except Exception as audit_error:
                logger.exception(
                    "Failed to persist HTTP query failure audit",
                    error=str(audit_error),
                    trace_id=trace_id,
                )
            raise
        except Exception as e:
            # Increment failure metrics and set span error status
            QUERY_COUNT.labels(tenant_id=request.tenant_id, status="failure").inc()
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            QUERY_LATENCY_SECS.labels(tenant_id=request.tenant_id).observe(latency_ms / 1000.0)
            span.record_exception(e)
            span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, str(e)))

            try:
                await session.rollback()
                from sqlalchemy import update

                await session.execute(
                    update(QueryTraceRow)
                    .where(QueryTraceRow.trace_id == trace_id)
                    .values(completed_at=func.now(), latency_ms=latency_ms)
                )
                await session.commit()
            except Exception as audit_error:
                logger.exception(
                    "Failed to persist query failure audit",
                    error=str(audit_error),
                    trace_id=trace_id,
                )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Query execution failed: {e!s}",
            ) from e
