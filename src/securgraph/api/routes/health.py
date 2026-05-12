"""Liveness / readiness endpoint.

M0 returns a static OK plus an empty components map. M1+ progressively wires
live pings to each backend (Postgres, Qdrant, Neo4j, OpenFGA, Ollama) under
the same shape.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import Field

from securgraph import __version__
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


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness/readiness probe",
    response_description="Service health and per-component status.",
)
async def health() -> JSONResponse:
    """Return overall health.

    M0 only reports the service is up; backends are reported as
    `not_implemented` and will be wired in their respective milestones.
    """
    from securgraph.config import get_settings

    settings = get_settings()

    components: dict[str, ComponentReport] = {
        "postgres": ComponentReport(status=ComponentStatus.NOT_IMPLEMENTED),
        "qdrant": ComponentReport(status=ComponentStatus.NOT_IMPLEMENTED),
        "neo4j": ComponentReport(status=ComponentStatus.NOT_IMPLEMENTED),
        "openfga": ComponentReport(status=ComponentStatus.NOT_IMPLEMENTED),
        "ollama": ComponentReport(status=ComponentStatus.NOT_IMPLEMENTED),
    }

    body = HealthResponse(
        status="ok",
        service=settings.service_name,
        version=__version__,
        env=settings.env,
        components=components,
    )
    return JSONResponse(status_code=status.HTTP_200_OK, content=body.model_dump(mode="json"))
