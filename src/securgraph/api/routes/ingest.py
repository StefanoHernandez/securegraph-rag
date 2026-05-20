"""FastAPI route for document ingestion.

Allows uploading documents via multipart/form-data and running the full M1 ingestion pipeline.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session
from securgraph.domain import Classification, DocumentType
from securgraph.domain.base import SGModel
from securgraph.ingestion.service import IngestionRequest, ingest_document

router = APIRouter(tags=["ingestion"])


class IngestResponse(SGModel):
    """API response schema for document ingestion."""

    job_id: str
    trace_id: str
    document_id: str
    status: str
    chunks_created: int


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and ingest a document",
)
async def ingest_file(
    file: Annotated[UploadFile, File(description="File bytes to parse")],
    tenant_id: Annotated[str, Form(description="Tenant scope identifier")],
    owner_id: Annotated[str, Form(description="Author/Owner user identifier")],
    document_type: Annotated[
        DocumentType, Form(description="Type of document (e.g. policy, manual)")
    ] = DocumentType.OTHER,
    classification: Annotated[
        Classification, Form(description="Security level classification")
    ] = Classification.INTERNAL,
    policy_tags: Annotated[str, Form(description="Comma-separated policy tags for ReBAC")] = "",
    title_override: Annotated[str | None, Form(description="Optional custom title")] = None,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> IngestResponse:
    """Run full ingestion pipeline: parse, chunk, bind policy, and index to Postgres."""
    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    tags = tuple(t.strip() for t in policy_tags.split(",") if t.strip())

    request = IngestionRequest(
        tenant_id=tenant_id,
        source_uri=f"file://{file.filename}",
        owner_id=owner_id,
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
