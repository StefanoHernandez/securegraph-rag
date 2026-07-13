"""Background ingestion task worker.

Provides the entrypoint and configuration for arq task execution.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from securgraph.adapters.embeddings import get_embedding_adapter
from securgraph.adapters.neo4j import get_neo4j_driver
from securgraph.api.dependencies import set_tenant_rls_context
from securgraph.api.main import enforce_audit_hygiene
from securgraph.config import get_settings
from securgraph.domain import Classification, DocumentType
from securgraph.indexing.postgres_writer import record_ingestion_saga_step, update_ingestion_job
from securgraph.ingestion.saga import SagaJournal
from securgraph.ingestion.service import (
    IngestionCompensationError,
    IngestionRequest,
    ingest_document,
)
from securgraph.logging import configure_logging, get_logger

_LOG = get_logger(__name__)


async def startup(ctx: dict[Any, Any]) -> None:
    """Bootstrap connection pools and external clients in the worker context."""
    settings = get_settings()
    enforce_audit_hygiene(settings)
    configure_logging(level=settings.log_level, json_logs=settings.env != "dev")
    _LOG.info("worker_startup_begun", env=settings.env)

    # 1. Postgres Session Maker
    engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
    ctx["engine"] = engine
    ctx["session_maker"] = async_sessionmaker(engine, expire_on_commit=False)

    # 2. Qdrant Client
    ctx["qdrant_client"] = QdrantClient(url=settings.qdrant_url)

    # 3. Embedding Adapter
    ctx["embeddings"] = get_embedding_adapter()

    # 4. Neo4j Async Driver
    ctx["neo4j_driver"] = get_neo4j_driver()
    _LOG.info("worker_startup_completed")


async def shutdown(ctx: dict[Any, Any]) -> None:
    """Clean teardown of connection pools and external clients."""
    _LOG.info("worker_shutdown_begun")
    if "engine" in ctx:
        await ctx["engine"].dispose()
    if "neo4j_driver" in ctx:
        await ctx["neo4j_driver"].close()
    _LOG.info("worker_shutdown_completed")


def build_saga_journal(session_maker: Any, *, job_id: str, tenant_id: str) -> SagaJournal:
    """Create a worker journal that survives the ingest transaction rollback."""

    async def journal(
        step: str,
        status: str,
        resource_ids: dict[str, object],
        compensation_status: str | None,
        error: str | None,
    ) -> None:
        async with session_maker() as journal_session:
            await set_tenant_rls_context(
                journal_session,
                tenant_id=tenant_id,
                is_super_admin=False,
            )
            await record_ingestion_saga_step(
                journal_session,
                job_id=job_id,
                tenant_id=tenant_id,
                step=step,
                status=status,
                resource_ids=resource_ids,
                compensation_status=compensation_status,
                error=error,
            )
            await journal_session.commit()

    return journal


def ingestion_failure_status(error: Exception) -> str:
    """Keep jobs out of automatic retry when an external residue is possible."""
    if isinstance(error, IngestionCompensationError):
        return "reconcile_required"
    return "failed"


async def async_ingest_task(
    ctx: dict[Any, Any],
    request_dict: dict[str, Any],
    job_id: str,
    trace_id: str,
) -> dict[str, Any]:
    """Background task running the full document ingestion pipeline."""
    print(f"!!! [WORKER] async_ingest_task started for job_id={job_id}, trace_id={trace_id} !!!")
    session_maker = ctx["session_maker"]

    # 1. Update job to running state in a separate short-lived transaction
    async with session_maker() as session:
        try:
            await set_tenant_rls_context(
                session,
                tenant_id=request_dict["tenant_id"],
                is_super_admin=False,
            )
            print("!!! [WORKER] updating status to running... !!!")
            await update_ingestion_job(
                session,
                job_id=job_id,
                status="running",
                increment_attempt=True,
            )
            await session.commit()
            print("!!! [WORKER] status updated and committed successfully !!!")
        except Exception as e:
            print(f"!!! [WORKER] FAILED to update status to running: {e} !!!")
            _LOG.exception("worker_failed_to_update_job_status_to_running", job_id=job_id)

    # 2. Reconstruct IngestionRequest and execute the ingestion pipeline
    async with session_maker() as session:
        try:
            await set_tenant_rls_context(
                session,
                tenant_id=request_dict["tenant_id"],
                is_super_admin=False,
            )
            print("!!! [WORKER] reconstructing IngestionRequest... !!!")
            request = IngestionRequest(
                tenant_id=request_dict["tenant_id"],
                source_uri=request_dict["source_uri"],
                owner_id=request_dict["owner_id"],
                data=request_dict["data"],
                document_type=DocumentType(request_dict["document_type"]),
                classification=Classification(request_dict["classification"]),
                policy_tags=tuple(request_dict["policy_tags"]),
                direct_viewers=tuple(request_dict.get("direct_viewers", ())),
                title_override=request_dict.get("title_override"),
                source_format=request_dict.get("source_format"),
            )
            print("!!! [WORKER] IngestionRequest reconstructed. Calling ingest_document... !!!")

            result = await ingest_document(
                session=session,
                request=request,
                job_id=job_id,
                trace_id=trace_id,
                qdrant_client=ctx.get("qdrant_client"),
                embeddings=ctx.get("embeddings"),
                neo4j_driver=ctx.get("neo4j_driver"),
                saga_journal=build_saga_journal(
                    session_maker,
                    job_id=job_id,
                    tenant_id=request_dict["tenant_id"],
                ),
            )
            print(f"!!! [WORKER] ingest_document returned successfully. status={result.status} !!!")

            # The transaction must be explicitly committed by the caller
            await session.commit()
            print("!!! [WORKER] transaction committed successfully !!!")
            _LOG.info("worker_ingestion_success", job_id=job_id, document_id=result.document_id)
            return {
                "job_id": result.job_id,
                "trace_id": result.trace_id,
                "document_id": result.document_id,
                "status": result.status.value,
                "chunks_created": result.chunks_created,
            }
        except Exception as e:
            print(f"!!! [WORKER] EXCEPTION OCCURRED DURING INGESTION: {e} !!!")
            import traceback

            traceback.print_exc()
            await session.rollback()
            _LOG.exception("worker_ingestion_failed", job_id=job_id, error=str(e))
            # Mark the job as failed in Postgres
            async with session_maker() as fail_session:
                await set_tenant_rls_context(
                    fail_session,
                    tenant_id=request_dict["tenant_id"],
                    is_super_admin=False,
                )
                await update_ingestion_job(
                    fail_session,
                    job_id=job_id,
                    status=ingestion_failure_status(e),
                    error=str(e),
                )
                await fail_session.commit()
            raise


async def anchor_due_ledgers(ctx: dict[Any, Any]) -> dict[str, int]:
    """Idempotently anchor tenants whose audit chain heads are due."""
    from sqlalchemy import select

    from securgraph.indexing.anchors import (
        anchor_current_chain_heads,
        should_anchor_current_chain_heads,
    )
    from securgraph.indexing.db import TenantRow

    session_maker = ctx["session_maker"]
    anchored = 0
    async with session_maker() as session:
        # Tenant discovery is the one cross-tenant operation. The actual
        # read/write for each chain immediately returns to tenant scope.
        await set_tenant_rls_context(session, tenant_id=None, is_super_admin=True)
        tenants = list((await session.execute(select(TenantRow.id))).scalars().all())
        for tenant_id in tenants:
            await set_tenant_rls_context(
                session,
                tenant_id=tenant_id,
                is_super_admin=False,
            )
            if not await should_anchor_current_chain_heads(session, tenant_id):
                continue
            await anchor_current_chain_heads(session, tenant_id)
            anchored += 1
        if anchored:
            await session.commit()
    _LOG.info("anchor_due_ledgers_completed", anchored=anchored)
    return {"anchored": anchored}


class WorkerSettings:
    """arq Worker configuration."""

    functions: ClassVar = [async_ingest_task, anchor_due_ledgers]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    cron_jobs: ClassVar = [
        cron(
            anchor_due_ledgers,
            minute=set(range(0, 60, max(1, get_settings().audit_anchor_interval_seconds // 60))),
            run_at_startup=False,
        )
    ]
