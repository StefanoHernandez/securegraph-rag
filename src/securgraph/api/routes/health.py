"""Zero-cost liveness/readiness and explicit privileged diagnostics."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from openfga_sdk import OpenFgaClient
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph import __version__
from securgraph.api.dependencies import get_db_session, get_openfga_client
from securgraph.api.routes.auth import (
    AuthenticatedPrincipal,
    require_super_admin,
    require_user,
)
from securgraph.config import get_settings
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
    checked_at: datetime | None = None


class LivenessResponse(SGModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class HealthResponse(SGModel):
    status: Literal["ok", "degraded", "down"]
    service: str
    version: str
    env: str
    components: dict[str, ComponentReport] = Field(default_factory=dict)


_provider_diagnostic: ComponentReport | None = None
_provider_diagnostic_at: datetime | None = None
_DIAGNOSTIC_CACHE_TTL = timedelta(minutes=15)


def _failed_report() -> ComponentReport:
    return ComponentReport(
        status=ComponentStatus.UNAVAILABLE,
        detail="check failed",
        checked_at=datetime.now(UTC),
    )


async def ping_postgres(session: AsyncSession) -> ComponentReport:
    start = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        return ComponentReport(
            status=ComponentStatus.OK,
            latency_ms=(time.perf_counter() - start) * 1000,
            checked_at=datetime.now(UTC),
        )
    except Exception:
        return _failed_report()


async def ping_qdrant() -> ComponentReport:
    from qdrant_client import QdrantClient

    settings = get_settings()
    start = time.perf_counter()

    def check() -> None:
        client = QdrantClient(url=settings.qdrant_url, check_compatibility=False)
        try:
            client.get_collection(collection_name=settings.qdrant_collection)
        finally:
            client.close()

    try:
        await asyncio.to_thread(check)
        return ComponentReport(
            status=ComponentStatus.OK,
            latency_ms=(time.perf_counter() - start) * 1000,
            checked_at=datetime.now(UTC),
        )
    except Exception:
        return _failed_report()


async def ping_neo4j() -> ComponentReport:
    start = time.perf_counter()
    try:
        from securgraph.adapters.neo4j import get_neo4j_driver

        await get_neo4j_driver().verify_connectivity()
        return ComponentReport(
            status=ComponentStatus.OK,
            latency_ms=(time.perf_counter() - start) * 1000,
            checked_at=datetime.now(UTC),
        )
    except Exception:
        return _failed_report()


async def ping_openfga(client: OpenFgaClient) -> ComponentReport:
    start = time.perf_counter()
    try:
        await client.read_latest_authorization_model()
        return ComponentReport(
            status=ComponentStatus.OK,
            latency_ms=(time.perf_counter() - start) * 1000,
            checked_at=datetime.now(UTC),
        )
    except Exception:
        return _failed_report()


async def ping_ollama() -> ComponentReport:
    """Explicitly validate the active provider; never called by readiness."""
    start = time.perf_counter()
    try:
        settings = get_settings()
        if settings.llm_provider == "ollama":
            from securgraph.adapters.llm.ollama import get_ollama_client

            await get_ollama_client().list()
        else:
            from securgraph.adapters.llm import get_llm_adapter

            await get_llm_adapter().chat(
                system="Reply with the single word: ok",
                user="ping",
                temperature=0.0,
            )
        return ComponentReport(
            status=ComponentStatus.OK,
            latency_ms=(time.perf_counter() - start) * 1000,
            checked_at=datetime.now(UTC),
        )
    except Exception:
        return _failed_report()


def _cached_provider_report() -> ComponentReport:
    if _provider_diagnostic is None or _provider_diagnostic_at is None:
        return ComponentReport(status=ComponentStatus.DEGRADED, detail="not_tested")
    if datetime.now(UTC) - _provider_diagnostic_at > _DIAGNOSTIC_CACHE_TTL:
        return ComponentReport(
            status=ComponentStatus.DEGRADED,
            detail="stale",
            latency_ms=_provider_diagnostic.latency_ms,
            checked_at=_provider_diagnostic.checked_at,
        )
    return _provider_diagnostic


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    settings = get_settings()
    return LivenessResponse(service=settings.service_name, version=__version__)


async def _readiness_body(session: AsyncSession, openfga_client: OpenFgaClient) -> HealthResponse:
    postgres, qdrant, neo4j, openfga = await asyncio.gather(
        ping_postgres(session),
        ping_qdrant(),
        ping_neo4j(),
        ping_openfga(openfga_client),
    )
    components = {
        "postgres": postgres,
        "qdrant": qdrant,
        "neo4j": neo4j,
        "openfga": openfga,
        "provider": _cached_provider_report(),
    }
    required = (postgres, qdrant, neo4j, openfga)
    overall = "down" if any(r.status == ComponentStatus.UNAVAILABLE for r in required) else "ok"
    settings = get_settings()
    return HealthResponse(
        status=overall,
        service=settings.service_name,
        version=__version__,
        env=settings.env,
        components=components,
    )


@router.get("/health/ready", response_model=HealthResponse)
async def readiness(
    session: AsyncSession = Depends(get_db_session),
    openfga_client: OpenFgaClient = Depends(get_openfga_client),
    _principal: AuthenticatedPrincipal | None = Depends(require_user),
) -> HealthResponse:
    return await _readiness_body(session, openfga_client)


@router.get("/health", response_model=HealthResponse, deprecated=True)
async def legacy_health(
    session: AsyncSession = Depends(get_db_session),
    openfga_client: OpenFgaClient = Depends(get_openfga_client),
    _principal: AuthenticatedPrincipal | None = Depends(require_user),
) -> JSONResponse:
    body = await _readiness_body(session, openfga_client)
    return JSONResponse(status_code=status.HTTP_200_OK, content=body.model_dump(mode="json"))


@router.post("/admin/diagnostics/providers/{provider}", response_model=ComponentReport)
async def provider_diagnostic(
    provider: str,
    _principal: AuthenticatedPrincipal | None = Depends(require_super_admin),
) -> ComponentReport:
    global _provider_diagnostic, _provider_diagnostic_at
    if provider != get_settings().llm_provider:
        raise HTTPException(status_code=400, detail="Provider is not active")
    if (
        _provider_diagnostic_at is not None
        and datetime.now(UTC) - _provider_diagnostic_at < _DIAGNOSTIC_CACHE_TTL
    ):
        return _provider_diagnostic or _cached_provider_report()
    _provider_diagnostic = await ping_ollama()
    _provider_diagnostic_at = datetime.now(UTC)
    return _provider_diagnostic
