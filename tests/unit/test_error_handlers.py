"""One error contract, including for the exceptions nobody planned for."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from securgraph.ingestion.service import IngestionError
from securgraph.policy.egress import EgressBlockedError

pytestmark = pytest.mark.unit

SENTINEL = "SELECT secret_column FROM classified_chunks WHERE tenant_id = 'mrd'"


@pytest.fixture
def app_with_probes(fastapi_app: FastAPI) -> FastAPI:
    """Mount routes that raise, so the handlers can be exercised directly."""

    @fastapi_app.get("/_probe/registered")
    async def _registered() -> None:
        raise EgressBlockedError("mrd", "openai", "judge", "default egress policy")

    @fastapi_app.get("/_probe/unregistered")
    async def _unregistered() -> None:
        raise RuntimeError(SENTINEL)

    @fastapi_app.get("/_probe/unexposed")
    async def _unexposed() -> None:
        raise IngestionError(SENTINEL)

    return fastapi_app


@pytest.fixture
async def probe_client(app_with_probes: FastAPI):
    # raise_app_exceptions=False: Starlette re-raises after the Exception
    # handler runs, so the default fixture would hand us the exception rather
    # than the response uvicorn would actually send.
    transport = ASGITransport(app=app_with_probes, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_registered_exception_uses_its_spec(probe_client: AsyncClient) -> None:
    response = await probe_client.get("/_probe/registered")
    body = response.json()
    assert response.status_code == 451
    assert body["code"] == "egress_blocked"
    assert "Egress blocked by tenant policy" in body["detail"]


async def test_unhandled_exception_never_leaks_its_text(probe_client: AsyncClient) -> None:
    response = await probe_client.get("/_probe/unregistered")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert SENTINEL not in response.text


async def test_unexposed_registered_exception_never_leaks_its_text(
    probe_client: AsyncClient,
) -> None:
    response = await probe_client.get("/_probe/unexposed")
    assert response.status_code == 500
    assert response.json()["code"] == "ingestion_failed"
    assert SENTINEL not in response.text


async def test_every_error_body_carries_code_and_request_id(
    probe_client: AsyncClient,
) -> None:
    for path in ("/_probe/registered", "/_probe/unregistered", "/_probe/unexposed"):
        response = await probe_client.get(path)
        body = response.json()
        assert set(body) == {"detail", "code", "request_id"}
        # request_id must survive on every path, including the unhandled one:
        # ServerErrorMiddleware runs OUTSIDE RequestIdMiddleware, so by then the
        # contextvar is already reset and only request.state still has it. This
        # is the path a user actually reports, so a null id here is useless.
        assert body["request_id"], f"{path} lost its request id"
        assert response.headers.get("X-Request-ID") == body["request_id"]


async def test_http_exception_keeps_its_detail_and_gains_a_code(
    client: AsyncClient,
) -> None:
    # 404 from the router itself, i.e. an HTTPException nobody wrote by hand.
    response = await client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"
    assert response.json()["code"] == "not_found"


async def test_csrf_rejection_uses_the_shared_error_shape(client: AsyncClient) -> None:
    # A cookie-authenticated unsafe request with no CSRF header. The response
    # comes from middleware, which bypasses the exception handlers entirely,
    # so its shape has to be maintained deliberately.
    #
    # Cookies go on the client, not on the request: httpx 0.28 deprecates
    # per-request cookies=, and tests/unit/test_csrf.py already sets them this
    # way.
    client.cookies.set("securgraph_access", "ambient-access-token")
    client.cookies.set("securgraph_csrf", "csrf-value")

    response = await client.post("/api/v1/query", json={})

    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "CSRF validation failed"
    assert body["code"] == "csrf_invalid"
    assert body["request_id"]
