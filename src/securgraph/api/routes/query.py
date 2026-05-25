"""FastAPI route for executing secure graph-RAG queries.

Allows querying the secure hybrid Graph-RAG system with unified consensus and ReBAC enforcement.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from securgraph.api.dependencies import get_graph_retriever, get_qdrant_retriever
from securgraph.domain import (
    ChannelSummary,
    ConfidenceBlock,
    QueryChannels,
    QueryRequest,
    QueryResponse,
    Subject,
    classify_query,
)
from securgraph.orchestration.workflow import compile_workflow

router = APIRouter(tags=["query"])


@router.post(
    "/query",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute a secure hybrid Graph-RAG query",
)
async def query_pipeline(
    request: QueryRequest,
    qdrant: Annotated[any, Depends(get_qdrant_retriever)],
    graph: Annotated[any, Depends(get_graph_retriever)],
) -> QueryResponse:
    """Run the 8-node LangGraph workflow with consensus verification and policy pruning."""
    trace_id = str(uuid.uuid4())

    subject = Subject(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
    )

    # Pre-classify query intent
    classification = classify_query(request.query)

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
        vector_count = sum(1 for ev in vector_context if getattr(ev, "retriever_id", None) == "vector")
        lexical_count = sum(1 for ev in vector_context if getattr(ev, "retriever_id", None) == "lexical")

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

        return QueryResponse(
            answer=final_answer.text,
            citations=final_answer.citations,
            confidence=confidence,
            channels=channels,
            trace_id=trace_id,
            warnings=tuple(warnings_list),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query execution failed: {e!s}",
        ) from e
