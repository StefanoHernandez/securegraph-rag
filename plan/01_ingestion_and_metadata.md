# Milestone 1: Ingestion & Metadata Completion Spec

Milestone 1 covers the core ingestion of files (Markdown, PDF, DOCX, Text), metadata extraction, stable document/chunk hashing, policy tagging, and PostgreSQL storage. 

This document specifies the remaining tasks to complete Milestone 1.

---

## 1. FastAPI `/ingest` Router

We will create `src/securgraph/api/routes/ingest.py` and register it in `src/securgraph/api/main.py`. The endpoint accepts standard multipart form data to allow file uploads along with metadata fields.

### 📝 Python Route Skeleton (`src/securgraph/api/routes/ingest.py`)

```python
from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, UploadFile, File, Form, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.api.dependencies import get_db_session  # to be implemented
from securgraph.domain import Classification, DocumentType, IngestionStatus
from securgraph.domain.base import SGModel
from securgraph.ingestion.service import IngestionRequest, ingest_document

router = APIRouter(tags=["ingestion"])

class IngestResponse(SGModel):
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
    document_type: Annotated[DocumentType, Form(description="Type of document (e.g. policy, manual)")] = DocumentType.OTHER,
    classification: Annotated[Classification, Form(description="Security level classification")] = Classification.INTERNAL,
    policy_tags: Annotated[str, Form(description="Comma-separated policy tags for ReBAC")] = "",
    title_override: Annotated[str | None, Form(description="Optional custom title")] = None,
    session: AsyncSession = Depends(get_db_session),
) -> IngestResponse:
    """Run full ingestion pipeline synchronously: parse, chunk, bind policy, index to Postgres."""
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
        # DB session is committed/rolled back by route wrapper or dependency lifetime
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
            detail=f"Ingestion pipeline failed: {str(e)}",
        )
```

---

## 2. Async PostgreSQL Database Dependency

Create `src/securgraph/api/dependencies.py` to manage transactional database sessions using SQLAlchemy `async_sessionmaker`.

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from securgraph.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Dependency to provide transactional async database sessions."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

---

## 3. Alembic Database Migrations

PostgreSQL tables are defined inside `src/securgraph/indexing/db.py` using SQLAlchemy Declarative Base `Base`. We must initialize and run migrations.

### ⚙ Alembic Setup Plan
1. Initialize alembic in project root:
   ```bash
   uv run alembic init --template async alembic
   ```
2. Configure `alembic.ini` to read database connection from Environment.
3. Edit `alembic/env.py` to import SQLAlchemy models:
   ```python
   from securgraph.indexing.db import Base
   target_metadata = Base.metadata
   ```
4. Generate the initial migration:
   ```bash
   uv run alembic revision --autogenerate -m "initial_schema"
   ```
5. Apply migrations:
   ```bash
   uv run alembic upgrade head
   ```

---

## 4. Live Health Check Probe

Update `src/securgraph/api/routes/health.py` to verify connection with PostgreSQL.

```python
import time
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
# ... other imports ...

async def ping_postgres(session: AsyncSession) -> ComponentReport:
    start = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        latency = (time.perf_counter() - start) * 1000.0
        return ComponentReport(status=ComponentStatus.OK, latency_ms=latency)
    except Exception as e:
        return ComponentReport(status=ComponentStatus.UNAVAILABLE, detail=str(e))
```

---

## 5. Ingestion Test Specification

We must write tests inside `tests/unit/test_ingestion.py` covering:
- **Parser Robustness**: Testing Markdown, text, PDF, DOCX parsers with edge-case structures.
- **Chunker Invariants**: Asserting token lengths fit within prompt budgets (`tiktoken` verification).
- **Idempotency Invariant**: Asserting that submitting identical bytes twice with the same URI yields `status = ALREADY_INGESTED` and 0 new chunks written.
