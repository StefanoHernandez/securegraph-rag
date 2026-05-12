"""Project-wide pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True, scope="session")
def _test_env_defaults() -> Iterator[None]:
    """Provide safe defaults so unit tests run without a populated .env."""
    os.environ.setdefault("SECURGRAPH_ENV", "test")
    os.environ.setdefault("SECURGRAPH_OTEL_ENABLED", "false")
    os.environ.setdefault("SECURGRAPH_LOG_LEVEL", "WARNING")
    # Clear cached Settings so the autouse env vars take effect on the first call.
    from securgraph.config import get_settings

    get_settings.cache_clear()
    yield


@pytest.fixture
def fastapi_app() -> FastAPI:
    """Fresh FastAPI app for tests (bypasses the process-level singleton)."""
    from securgraph.api.main import create_app

    return create_app()


@pytest.fixture
async def client(fastapi_app: FastAPI) -> AsyncIterator[AsyncClient]:
    """ASGI client that exercises the app without binding a socket."""
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
