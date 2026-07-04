"""FastAPI application factory and ASGI entry point.

Run with: ``uvicorn securgraph.api.main:app --reload``.

Application wiring:
- M0: only the ``/health`` router + OpenTelemetry FastAPI instrumentation.
- M1+: progressively mount the ingest / query / explain / audit routers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from securgraph import __version__
from securgraph.api.middleware import CSRFMiddleware
from securgraph.api.routes import admin as admin_routes
from securgraph.api.routes import auth as auth_routes
from securgraph.api.routes import config as config_routes
from securgraph.api.routes import control_plane as control_plane_routes
from securgraph.api.routes import explain as explain_routes
from securgraph.api.routes import health as health_routes
from securgraph.api.routes import ingest as ingest_routes
from securgraph.api.routes import operations as operations_routes
from securgraph.api.routes import query as query_routes
from securgraph.api.routes import settings as settings_routes
from securgraph.config import DEFAULT_JWT_SECRET, Settings, get_settings
from securgraph.logging import configure_logging, get_logger
from securgraph.observability import setup_observability, shutdown_observability


def enforce_secret_hygiene(settings: Settings) -> None:
    """Fail closed at startup when secrets are unsafe for stage/prod.

    In dev/test a weak or default JWT secret is tolerated (logged as a
    warning by the caller); in stage/prod it would let anyone forge session
    tokens, so the process must refuse to boot.
    """
    if settings.env not in ("stage", "prod"):
        return
    jwt_secret = settings.jwt_secret.get_secret_value()
    if jwt_secret == DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "SECURGRAPH_JWT_SECRET is the known-public default; refusing to start "
            f"in env={settings.env}. Set a random secret of at least 32 characters."
        )
    if len(jwt_secret) < 32:
        raise RuntimeError(
            f"SECURGRAPH_JWT_SECRET is too short ({len(jwt_secret)} chars); refusing "
            f"to start in env={settings.env}. Use at least 32 characters."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Process-level startup/shutdown.

    Called once per uvicorn worker, around the request loop.
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.env != "dev")
    log = get_logger(__name__)
    enforce_secret_hygiene(settings)
    if (
        settings.env in ("dev", "test")
        and settings.jwt_secret.get_secret_value() == DEFAULT_JWT_SECRET
    ):
        log.warning("jwt_secret_default_in_use", env=settings.env)
    log.info(
        "securgraph_starting",
        version=__version__,
        env=settings.env,
        service=settings.service_name,
    )

    setup_observability(settings)

    # Bootstrap OpenFGA
    try:
        from securgraph.api.dependencies import bootstrap_openfga

        await bootstrap_openfga(settings)
    except Exception:
        log.exception("openfga_bootstrapping_failed")
        if settings.env in ("prod", "stage"):
            raise

    # Reload persisted runtime-safe settings so PUT-applied overrides
    # survive restarts. Non-fatal: the API still serves with defaults.
    try:
        from securgraph.api.dependencies import AsyncSessionLocal
        from securgraph.runtime_overrides import hydrate_overrides_from_db

        async with AsyncSessionLocal() as session:
            await hydrate_overrides_from_db(session)
    except Exception:
        log.exception("runtime_overrides_hydration_failed")

    try:
        yield
    finally:
        log.info("securgraph_stopping")
        try:
            from securgraph.adapters.neo4j import close_neo4j_driver

            await close_neo4j_driver()
        except Exception:
            log.exception("neo4j_driver_teardown_failed")
        try:
            from securgraph.api.dependencies import _redis_pool

            if _redis_pool is not None:
                await _redis_pool.close()
        except Exception:
            log.exception("redis_pool_teardown_failed")
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

    application.add_middleware(CSRFMiddleware)

    # The normal browser path is the same-origin Next.js BFF. Explicit origins
    # remain available in development for direct API diagnostics.
    if settings.env == "dev":
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    routers = (
        health_routes.router,
        ingest_routes.router,
        operations_routes.router,
        query_routes.router,
        explain_routes.router,
        auth_routes.router,
        admin_routes.router,
        config_routes.router,
        control_plane_routes.router,
        settings_routes.router,
    )
    versioned = APIRouter(prefix="/api/v1")
    for router in routers:
        versioned.include_router(router)
    application.include_router(versioned)

    # Temporary alpha compatibility aliases. They are intentionally omitted
    # from OpenAPI; all generated clients and the UI use /api/v1.
    for router in routers:
        application.include_router(router, include_in_schema=False)

    # OTel FastAPI instrumentation wraps every request in a server span and
    # injects the trace context downstream.
    FastAPIInstrumentor.instrument_app(application)

    return application


app: FastAPI = create_app()
