"""Background ingestion task worker.

Provides the entrypoint and configuration for arq task execution.
"""

from __future__ import annotations

import asyncio
from typing import Any

from arq.connections import RedisSettings
from neo4j import AsyncDriver
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from securgraph.adapters.embeddings import get_embedding_adapter
from securgraph.adapters.neo4j import get_neo4j_driver
from securgraph.config import get_settings
from securgraph.domain import Classification, DocumentType
from securgraph.indexing.postgres_writer import update_ingestion_job
from securgraph.ingestion.service import IngestionRequest, ingest_document
from securgraph.logging import configure_logging, get_logger

_LOG = get_logger(__name__)


async def startup(ctx: dict[Any, Any]) -> None:
    """Bootstrap connection pools and external clients in the worker context."""
    settings = get_settings()
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
            print("!!! [WORKER] updating status to running... !!!")
            await update_ingestion_job(
                session,
                job_id=job_id,
                status="running",
            )
            await session.commit()
            print("!!! [WORKER] status updated and committed successfully !!!")
        except Exception as e:
            print(f"!!! [WORKER] FAILED to update status to running: {e} !!!")
            _LOG.exception("worker_failed_to_update_job_status_to_running", job_id=job_id)

    # 2. Reconstruct IngestionRequest and execute the ingestion pipeline
    async with session_maker() as session:
        try:
            print("!!! [WORKER] reconstructing IngestionRequest... !!!")
            request = IngestionRequest(
                tenant_id=request_dict["tenant_id"],
                source_uri=request_dict["source_uri"],
                owner_id=request_dict["owner_id"],
                data=request_dict["data"],
                document_type=DocumentType(request_dict["document_type"]),
                classification=Classification(request_dict["classification"]),
                policy_tags=tuple(request_dict["policy_tags"]),
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
                await update_ingestion_job(
                    fail_session,
                    job_id=job_id,
                    status="failed",
                    error=str(e),
                )
                await fail_session.commit()
            raise


class WorkerSettings:
    """arq Worker configuration."""

    functions = [async_ingest_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
