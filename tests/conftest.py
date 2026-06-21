"""Project-wide pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True, scope="session")
def _test_env_defaults() -> Iterator[None]:
    """Provide safe defaults so unit tests run without a populated .env.

    Uses ``setdefault`` so explicit env vars still win, but flags that the
    production ``.env`` may enable (tamper-evident audit, self-healing,
    async workers, communities) are forced OFF for unit tests because they
    require real backends or extra DB round-trips the mocks don't model.
    """
    os.environ.setdefault("SECURGRAPH_ENV", "test")
    os.environ.setdefault("SECURGRAPH_OTEL_ENABLED", "false")
    os.environ.setdefault("SECURGRAPH_LOG_LEVEL", "WARNING")
    # Flags that break unit-test isolation when the real .env enables them.
    os.environ.setdefault("SECURGRAPH_AUDIT_TAMPER_EVIDENT", "false")
    os.environ.setdefault("SECURGRAPH_FEATURE_SELF_HEALING_ENABLED", "false")
    os.environ.setdefault("SECURGRAPH_FEATURE_WORKERS_ENABLED", "false")
    os.environ.setdefault("SECURGRAPH_FEATURE_COMMUNITIES_ENABLED", "false")
    os.environ.setdefault("SECURGRAPH_FEATURE_VERIFIER_ENSEMBLE", "false")
    # Clear cached Settings so the autouse env vars take effect on the first call.
    from securgraph.config import get_settings

    get_settings.cache_clear()
    yield


@pytest.fixture
def fastapi_app() -> FastAPI:
    """Fresh FastAPI app for tests (bypasses the process-level singleton)."""
    from unittest.mock import AsyncMock

    from securgraph.api.dependencies import get_db_session
    from securgraph.api.main import create_app

    app = create_app()

    async def mock_db_session() -> AsyncIterator[AsyncMock]:
        mock = AsyncMock()
        mock.execute.return_value = None
        yield mock

    app.dependency_overrides[get_db_session] = mock_db_session
    return app


@pytest.fixture
async def client(fastapi_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """ASGI client that exercises the app without binding a socket."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def cleanup_engine() -> AsyncIterator[None]:
    """Dispose global database engine after each test to avoid closed event loop errors."""
    yield
    from securgraph.api.dependencies import engine

    await engine.dispose()
    # Also clear the LLM adapter cache so provider swaps in one test don't leak
    # into the next.
    try:
        from securgraph.adapters.llm import reset_llm_adapter_cache

        reset_llm_adapter_cache()
    except Exception:
        pass
