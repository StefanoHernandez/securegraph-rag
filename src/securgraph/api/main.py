"""FastAPI application factory and ASGI entry point.

Run with: ``uvicorn securgraph.api.main:app --reload``.

Application wiring:
- M0: only the ``/health`` router + OpenTelemetry FastAPI instrumentation.
- M1+: progressively mount the ingest / query / explain / audit routers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from securgraph import __version__
from securgraph.api.routes import health as health_routes
from securgraph.config import get_settings
from securgraph.logging import configure_logging, get_logger
from securgraph.observability import setup_observability, shutdown_observability


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Process-level startup/shutdown.

    Called once per uvicorn worker, around the request loop.
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.env != "dev")
    log = get_logger(__name__)
    log.info(
        "securgraph_starting",
        version=__version__,
        env=settings.env,
        service=settings.service_name,
    )

    setup_observability(settings)

    try:
        yield
    finally:
        log.info("securgraph_stopping")
        shutdown_observability()


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Factory function so tests can spin up fresh instances; ``app`` below is the
    process-level singleton uvicorn loads.
    """
    settings = get_settings()
    application = FastAPI(
        title="SecurGraph RAG",
        version=__version__,
        summary="Trustworthy hybrid vector-graph RAG with verifiable access control.",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # CORS open in dev so the future Next.js frontend (port 3000/3001) can call
    # the API without TLS plumbing. Production deployments override this list.
    if settings.env == "dev":
        application.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    application.include_router(health_routes.router)

    # OTel FastAPI instrumentation wraps every request in a server span and
    # injects the trace context downstream.
    FastAPIInstrumentor.instrument_app(application)

    return application


app: FastAPI = create_app()
