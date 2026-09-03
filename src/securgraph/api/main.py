"""FastAPI application factory and ASGI entry point.

Run with: ``uvicorn securgraph.api.main:app --reload``.

Application wiring:
- M0: only the ``/health`` router + OpenTelemetry FastAPI instrumentation.
- M1+: progressively mount the ingest / query / explain / audit routers.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from securgraph import __version__
from securgraph.api.middleware import CSRFMiddleware, SecurityHeadersMiddleware
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
from securgraph.config import (
    DEFAULT_JWT_SECRET,
    DEFAULT_NEO4J_PASSWORD,
    DEFAULT_POSTGRES_CREDENTIALS,
    Settings,
    get_settings,
)
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
            f"in env={settings.env}. Use at least 32 characters."
        )
    # The master key is the KEK for every provider credential in the database.
    # securgraph.security.secrets derives an AES-GCM key from it, so a short
    # human-memorable passphrase is brute-forceable offline from one ciphertext.
    master_key = settings.master_key.get_secret_value()
    if master_key and len(master_key) < 32:
        raise RuntimeError(
            f"SECURGRAPH_MASTER_KEY is too short ({len(master_key)} chars); refusing "
            f"in env={settings.env}. Use at least 32 random characters."
        )


def enforce_datastore_hygiene(settings: Settings) -> None:
    """Fail closed at startup when a backing store would accept anonymous callers.

    Every guarantee this system makes is enforced one layer above the
    datastores: OpenFGA decides authorization (C1), PostgreSQL RLS isolates
    tenants, Qdrant holds the chunk text those checks are meant to gate. A
    store reachable without credentials makes all of it decorative — an
    attacker writes their own OpenFGA tuples, or reads chunk payloads straight
    out of Qdrant, and the audit ledger still records "authorized".

    The local compose stack ships unauthenticated on purpose, so dev/test are
    exempt; stage/prod must not boot against it.
    """
    if settings.env not in ("stage", "prod"):
        return

    problems: list[str] = []

    if not settings.openfga_api_token.get_secret_value():
        problems.append(
            "SECURGRAPH_OPENFGA_API_TOKEN is empty: the ReBAC ground truth would "
            "accept unauthenticated tuple writes. Set it to match the server's "
            "OPENFGA_AUTHN_PRESHARED_KEYS."
        )
    if not settings.qdrant_api_key.get_secret_value():
        problems.append(
            "SECURGRAPH_QDRANT_API_KEY is empty: chunk payloads would be readable "
            "without authentication, bypassing every OpenFGA check."
        )
    neo4j_password = settings.neo4j_password.get_secret_value()
    if not neo4j_password or neo4j_password == DEFAULT_NEO4J_PASSWORD:
        problems.append(
            "SECURGRAPH_NEO4J_PASSWORD is empty or the known-public default; "
            "rotate it before deploying."
        )

    parsed_dsn = urlparse(settings.postgres_dsn)
    dsn_credentials = (parsed_dsn.username or "", parsed_dsn.password or "")
    if not parsed_dsn.password or dsn_credentials in DEFAULT_POSTGRES_CREDENTIALS:
        problems.append(
            "SECURGRAPH_POSTGRES_DSN carries no password or the known-public "
            "default credentials; RLS does not protect a database an attacker "
            "can log into directly."
        )

    # enforce_worker_hygiene already requires workers in stage/prod, so Redis
    # is always on the path here; guard it anyway rather than depend on order.
    if settings.feature_workers_enabled and not urlparse(settings.redis_url).password:
        problems.append(
            "SECURGRAPH_REDIS_URL has no password: the ingestion queue carries "
            "tenant document payloads."
        )

    if problems:
        raise RuntimeError(
            f"Refusing to start in env={settings.env} — unauthenticated datastore "
            "configuration: " + " ".join(problems)
        )


def enforce_auth_hygiene(settings: Settings) -> None:
    """Fail closed at startup when authentication is disabled in stage/prod.

    With ``auth_enabled=False``, ``require_user`` returns None and protected
    routes become effectively public. Dev/test may disable auth for hermetic
    tests and MRD eval seeding; stage/prod must never boot without auth.
    """
    if settings.env in ("stage", "prod") and not settings.auth_enabled:
        raise RuntimeError(
            "SECURGRAPH_AUTH_ENABLED=false; refusing to start "
            f"in env={settings.env}. Authentication must remain enabled."
        )


def enforce_worker_hygiene(settings: Settings) -> None:
    """Require the durable ARQ ingest path in stage and production.

    The synchronous path is intentionally kept for local development and
    hermetic tests. It cannot persist the cross-store saga journal in an
    independent transaction, so release deployments must route ingest through
    workers.
    """
    if settings.env in ("stage", "prod") and not settings.feature_workers_enabled:
        raise RuntimeError(
            "SECURGRAPH_FEATURE_WORKERS_ENABLED=true is required in stage/prod "
            "for the durable ingestion saga."
        )


# Model names that only exist on a local Ollama daemon. Sending one of these
# to a cloud provider is always a misconfiguration (the request 404s at query
# time with no obvious cause) — catch it at startup instead. Ollama itself is
# intentionally unavailable in stage/prod: it is a legacy local adapter, not a
# submission or release provider.
_OLLAMA_SHAPED_MODEL = re.compile(r"^(gemma|qwen|granite|llama|phi|deepseek)|.+:", re.I)
_CLOUD_PROVIDERS = frozenset({"openai", "mistral", "anthropic", "google"})


def check_provider_model_coherence(settings: Settings) -> list[str]:
    """Return a warning per (provider, model) pair that cannot work together.

    Raises RuntimeError in stage/prod (fail-closed); the caller logs the
    warnings in dev/test.
    """
    pairs = [
        ("generation", settings.llm_provider, settings.llm_model),
        # Empty per-stage provider means "inherit the main provider".
        (
            "extract",
            settings.llm_provider_extract or settings.llm_provider,
            settings.llm_model_extract,
        ),
        (
            "judge",
            settings.llm_provider_judge or settings.llm_provider,
            settings.llm_model_judge,
        ),
    ]
    problems: list[str] = []
    for stage, provider, model in pairs:
        if provider == "ollama":
            problems.append(
                f"LLM {stage} stage: provider 'Ollama' is a legacy local adapter; "
                "use Mistral or OpenAI for stage/prod."
            )
        elif provider in _CLOUD_PROVIDERS and _OLLAMA_SHAPED_MODEL.match(model):
            problems.append(
                f"LLM {stage} stage: provider '{provider}' with model '{model}' — "
                "this looks like a local Ollama model name and will 404 on the "
                "cloud API. Set the matching SECURGRAPH_LLM_MODEL* variable."
            )
    if problems and settings.env in ("stage", "prod"):
        raise RuntimeError("; ".join(problems))
    return problems


def enforce_audit_hygiene(settings: Settings) -> None:
    """Fail closed in stage/prod when tamper-evident audit or MinIO anchoring is incomplete."""
    if settings.env not in ("stage", "prod"):
        return
    if not settings.audit_tamper_evident:
        raise RuntimeError(
            "SECURGRAPH_AUDIT_TAMPER_EVIDENT must be enabled; refusing to start "
            f"in env={settings.env}."
        )
    if len(settings.audit_hmac_key.get_secret_value()) < 32:
        raise RuntimeError(
            "AUDIT_HMAC_KEY is missing or too short; refusing to start "
            f"in env={settings.env}."
        )
    if settings.audit_anchor_backend != "minio":
        raise RuntimeError(
            "MinIO Object Lock anchor configuration is required; set "
            "SECURGRAPH_AUDIT_ANCHOR_BUCKET and SECURGRAPH_AUDIT_ANCHOR_ENDPOINT."
        )
    secret = settings.audit_anchor_secret_key.get_secret_value()
    if not settings.audit_anchor_access_key or not secret:
        raise RuntimeError(
            "SECURGRAPH_AUDIT_ANCHOR_ACCESS_KEY and SECURGRAPH_AUDIT_ANCHOR_SECRET_KEY "
            f"are required in env={settings.env}."
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
    enforce_datastore_hygiene(settings)
    enforce_auth_hygiene(settings)
    enforce_worker_hygiene(settings)
    enforce_audit_hygiene(settings)
    if (
        settings.env in ("dev", "test")
        and settings.jwt_secret.get_secret_value() == DEFAULT_JWT_SECRET
    ):
        log.warning("jwt_secret_default_in_use", env=settings.env)
    for problem in check_provider_model_coherence(settings):
        log.warning("llm_provider_model_mismatch", detail=problem)
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
        try:
            from securgraph.api.dependencies import close_qdrant_retriever

            close_qdrant_retriever()
        except Exception:
            log.exception("qdrant_retriever_teardown_failed")
        try:
            from securgraph.api.dependencies import close_openfga_client

            await close_openfga_client()
        except Exception:
            log.exception("openfga_client_teardown_failed")
        shutdown_observability()


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Factory function so tests can spin up fresh instances; ``app`` below is the
    process-level singleton uvicorn loads.
    """
    settings = get_settings()
    # The interactive docs enumerate every route, parameter and model of a
    # system whose entire claim is controlled access. Useful while developing,
    # free reconnaissance in a deployment — and the schema is published in the
    # repository for anyone who legitimately needs it.
    expose_docs = settings.env not in ("stage", "prod")
    application = FastAPI(
        title="SecurGraph RAG",
        version=__version__,
        summary="Trustworthy hybrid vector-graph RAG with verifiable access control.",
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )

    application.add_middleware(CSRFMiddleware)
    application.add_middleware(SecurityHeadersMiddleware)
    if settings.allowed_hosts:
        application.add_middleware(
            TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts
        )

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

    # Temporary alpha compatibility aliases, kept only outside stage/prod.
    # Every path-keyed guard in the codebase is written for /api/v1 — see
    # _MUST_CHANGE_ALLOWED_PATHS in api/routes/auth.py — so mounting an
    # unversioned copy silently doubles the surface those checks reason
    # about. Generated clients and the UI use /api/v1 only.
    if settings.env not in ("stage", "prod"):
        for router in routers:
            application.include_router(router, include_in_schema=False)

    # OTel FastAPI instrumentation wraps every request in a server span and
    # injects the trace context downstream.
    FastAPIInstrumentor.instrument_app(application)

    return application


app: FastAPI = create_app()
