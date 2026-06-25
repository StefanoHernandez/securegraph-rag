"""FastAPI route for document ingestion.

Allows uploading documents via multipart/form-data and running the full M1 ingestion pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session, get_redis_pool
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.config import get_settings
from securgraph.domain import Classification, DocumentType
from securgraph.domain.base import SGModel
from securgraph.indexing.db import AuthUserRow, IngestionJobRow, TenantMembershipRow
from securgraph.indexing.postgres_writer import create_ingestion_job
from securgraph.ingestion.metadata import derive_document_id
from securgraph.ingestion.metadata import source_hash as compute_source_hash
from securgraph.ingestion.service import IngestionRequest, ingest_document

router = APIRouter(tags=["ingestion"])


class IngestResponse(SGModel):
    """API response schema for document ingestion."""

    job_id: str
    trace_id: str
    document_id: str
    status: str
    chunks_created: int


class IngestionJobStatusResponse(SGModel):
    """API response schema for checking job status."""

    job_id: str
    trace_id: str
    tenant_id: str
    document_id: str | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    error: str | None


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and ingest a document",
)
async def ingest_file(
    file: Annotated[UploadFile, File(description="File bytes to parse")],
    tenant_id: Annotated[str, Form(description="Tenant scope identifier")],
    owner_id: Annotated[
        str | None, Form(description="Optional owner; defaults to authenticated uploader")
    ],
    response: Response,
    document_type: Annotated[
        DocumentType, Form(description="Type of document (e.g. policy, manual)")
    ] = DocumentType.OTHER,
    classification: Annotated[
        Classification, Form(description="Security level classification")
    ] = Classification.INTERNAL,
    policy_tags: Annotated[str, Form(description="Comma-separated policy tags for ReBAC")] = "",
    title_override: Annotated[str | None, Form(description="Optional custom title")] = None,
    session: AsyncSession = Depends(get_db_session),
    redis: ArqRedis = Depends(get_redis_pool),
    current_user: AuthenticatedPrincipal | None = Depends(require_user),
) -> IngestResponse:
    """Run full ingestion pipeline: parse, chunk, bind policy, and index."""
    if current_user is not None and not current_user.can_access_tenant(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token tenant does not match the ingestion tenant.",
        )
    effective_owner = owner_id or (
        f"user:{current_user.user_id}" if current_user is not None else "user:anonymous"
    )
    if ":" in effective_owner and not effective_owner.startswith("user:"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "owner_id cannot contain ':' unless prefixed with 'user:' to prevent "
                "FGA-namespace ambiguity."
            ),
        )
    if current_user is not None:
        requested_owner_id = effective_owner.removeprefix("user:")
        if requested_owner_id != current_user.user_id:
            if not current_user.can_admin_tenant(tenant_id):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Only tenant administrators may delegate document ownership.",
                )
            target_user = await session.get(AuthUserRow, requested_owner_id)
            target_membership = await session.get(
                TenantMembershipRow, (tenant_id, requested_owner_id)
            )
            if (
                target_user is None
                or not target_user.is_active
                or target_membership is None
                or not target_membership.is_active
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Delegated owner is not an active member of this tenant.",
                )

    settings = get_settings()
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # Ingestion Budget Limit check: fail-closed if document is > 50MB to prevent DoS
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Document size exceeds the 50MB ingestion budget limit.",
        )

    tags = tuple(t.strip() for t in policy_tags.split(",") if t.strip())

    if settings.feature_workers_enabled:
        response.status_code = status.HTTP_202_ACCEPTED
        job_id = f"ing_{uuid.uuid4().hex[:16]}"
        trace_id = f"trace_{uuid.uuid4().hex[:16]}"

        # Determine stable document_id immediately
        src_hash = compute_source_hash(data)
        document_id = derive_document_id(tenant_id, f"file://{file.filename}", src_hash)

        # Create queued job state in Postgres and commit immediately
        await create_ingestion_job(
            session,
            job_id=job_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            document_id=document_id,
            requested_by=current_user.user_id if current_user else None,
            owner_id=effective_owner.removeprefix("user:"),
            status="queued",
        )
        await session.commit()

        # Serialize request parameters for arq
        request_dict = {
            "tenant_id": tenant_id,
            "source_uri": f"file://{file.filename}",
            "owner_id": effective_owner,
            "data": data,
            "document_type": document_type.value,
            "classification": classification.value,
            "policy_tags": list(tags),
            "title_override": title_override,
            "source_format": None,
        }

        # Enqueue the background task
        await redis.enqueue_job("async_ingest_task", request_dict, job_id, trace_id)

        return IngestResponse(
            job_id=job_id,
            trace_id=trace_id,
            document_id=document_id,
            status="queued",
            chunks_created=0,
        )

    request = IngestionRequest(
        tenant_id=tenant_id,
        source_uri=f"file://{file.filename}",
        owner_id=effective_owner,
        data=data,
        document_type=document_type,
        classification=classification,
        policy_tags=tags,
        title_override=title_override,
    )

    try:
        # DB session is committed/rolled back by get_db_session dependency lifetime
        result = await ingest_document(session, request)
        return IngestResponse(
            job_id=result.job_id,
            trace_id=result.trace_id,
            document_id=result.document_id,
            status=result.status.value,
            chunks_created=result.chunks_created,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion pipeline failed: {e!s}",
        ) from e


@router.get(
    "/ingest/status/{job_id}",
    response_model=IngestionJobStatusResponse,
    summary="Get status of an ingestion job",
)
async def get_ingest_status(
    job_id: str,
    session: AsyncSession = Depends(get_db_session),
    current_user: AuthenticatedPrincipal | None = Depends(require_user),
) -> IngestionJobStatusResponse:
    """Look up current state and execution metrics of an ingestion job."""
    row = await session.get(IngestionJobRow, job_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ingestion job {job_id} not found.",
        )
    if current_user is not None:
        if not current_user.can_access_tenant(row.tenant_id):
            raise HTTPException(status_code=403, detail="Access denied to this ingestion job.")
        owns_job = current_user.user_id in {row.requested_by, row.owner_id}
        if not owns_job and not current_user.can_admin_tenant(row.tenant_id):
            raise HTTPException(status_code=403, detail="Access denied to this ingestion job.")
    return IngestionJobStatusResponse(
        job_id=row.job_id,
        trace_id=row.trace_id,
        tenant_id=row.tenant_id,
        document_id=row.document_id,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error=row.error,
    )
