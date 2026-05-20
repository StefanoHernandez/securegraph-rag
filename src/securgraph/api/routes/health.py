"""Liveness / readiness endpoint.

M0 returns a static OK plus an empty components map. M1+ progressively wires
live pings to each backend (Postgres, Qdrant, Neo4j, OpenFGA, Ollama) under
the same shape.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph import __version__
from securgraph.api.dependencies import get_db_session
from securgraph.domain.base import SGModel

router = APIRouter(tags=["meta"])


class ComponentStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    NOT_IMPLEMENTED = "not_implemented"


class ComponentReport(SGModel):
    status: ComponentStatus
    detail: str | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)


class HealthResponse(SGModel):
    status: Literal["ok", "degraded", "down"]
    service: str
    version: str
    env: str
    components: dict[str, ComponentReport] = Field(default_factory=dict)


async def ping_postgres(session: AsyncSession) -> ComponentReport:
    """Check connectivity to PostgreSQL."""
    start = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        latency = (time.perf_counter() - start) * 1000.0
        return ComponentReport(status=ComponentStatus.OK, latency_ms=latency)
    except Exception as e:
        return ComponentReport(status=ComponentStatus.UNAVAILABLE, detail=str(e))


async def ping_qdrant() -> ComponentReport:
    """Check connectivity to Qdrant by pinging the configured collection."""
    from qdrant_client import QdrantClient

    from securgraph.config import get_settings

    start = time.perf_counter()
    try:
        settings = get_settings()
        client = QdrantClient(url=settings.qdrant_url)
        # Attempt to retrieve collection info to verify connection and existence
        client.get_collection(collection_name=settings.qdrant_collection)
        latency = (time.perf_counter() - start) * 1000.0
        return ComponentReport(status=ComponentStatus.OK, latency_ms=latency)
    except Exception as e:
        return ComponentReport(status=ComponentStatus.UNAVAILABLE, detail=str(e))


async def ping_neo4j() -> ComponentReport:
    """Check connectivity to Neo4j database."""
    start = time.perf_counter()
    try:
        from securgraph.adapters.neo4j import get_neo4j_driver
        driver = get_neo4j_driver()
        await driver.verify_connectivity()
        latency = (time.perf_counter() - start) * 1000.0
        return ComponentReport(status=ComponentStatus.OK, latency_ms=latency)
    except Exception as e:
        return ComponentReport(status=ComponentStatus.UNAVAILABLE, detail=str(e))


async def ping_ollama() -> ComponentReport:
    """Check connectivity to local Ollama instance."""
    start = time.perf_counter()
    try:
        from securgraph.adapters.ollama import get_ollama_client
        client = get_ollama_client()
        await client.list()
        latency = (time.perf_counter() - start) * 1000.0
        return ComponentReport(status=ComponentStatus.OK, latency_ms=latency)
    except Exception as e:
        return ComponentReport(status=ComponentStatus.UNAVAILABLE, detail=str(e))


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness/readiness probe",
    response_description="Service health and per-component status.",
)
async def health(
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> JSONResponse:
    """Return overall health."""
    from securgraph.config import get_settings

    settings = get_settings()

    postgres_report = await ping_postgres(session)
    qdrant_report = await ping_qdrant()
    neo4j_report = await ping_neo4j()
    ollama_report = await ping_ollama()

    components: dict[str, ComponentReport] = {
        "postgres": postgres_report,
        "qdrant": qdrant_report,
        "neo4j": neo4j_report,
        "openfga": ComponentReport(status=ComponentStatus.NOT_IMPLEMENTED),
        "ollama": ollama_report,
    }

    overall_status = "ok"
    if (
        postgres_report.status == ComponentStatus.UNAVAILABLE
        or qdrant_report.status == ComponentStatus.UNAVAILABLE
        or neo4j_report.status == ComponentStatus.UNAVAILABLE
        or ollama_report.status == ComponentStatus.UNAVAILABLE
    ):
        overall_status = "down"

    body = HealthResponse(
        status=overall_status,
        service=settings.service_name,
        version=__version__,
        env=settings.env,
        components=components,
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content=body.model_dump(mode="json"))
