"""Registry mapping domain exceptions to their client-facing form.

A registry rather than a common `AppError` base class: six sites catch
`ValueError`/`RuntimeError` generically and rely on the current bases
(`api/routes/settings.py:198`, `:212`, `api/routes/admin.py:205`,
`policy/egress.py:169`, `indexing/tamper.py:165`,
`adapters/llm/base.py:296`). Reparenting would silently change which handler
wins at each of them.

`expose_message` decides whether `str(exc)` may leave the process. It is
False by default. Setting it True asserts that the message is composed
entirely of values the caller supplied or is entitled to see — see the table
in docs/superpowers/specs/2026-09-03-p2-error-surface-design.md §3.2.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from securgraph.api.request_context import REQUEST_ID_HEADER, get_request_id
from securgraph.corpus.manifest import CorpusManifestError
from securgraph.domain.query import QueryScopeError
from securgraph.ingestion.limits import MAX_UPLOAD_BYTES, UploadTooLargeError
from securgraph.ingestion.service import IngestionError
from securgraph.policy.egress import EgressBlockedError
from securgraph.security.secrets import SecretEncryptionError
from securgraph.services.provider_catalog import EgressUrlError


@dataclass(frozen=True)
class ErrorSpec:
    """How one exception type is presented to the caller."""

    code: str
    status: int
    message: str
    expose_message: bool = False


ERROR_MAP: dict[type[Exception], ErrorSpec] = {
    EgressBlockedError: ErrorSpec(
        code="egress_blocked",
        status=451,
        message="This request was blocked by the tenant egress policy.",
        # provider, stage and a fixed reason string: a policy outcome stated
        # to the tenant that configured the policy.
        expose_message=True,
    ),
    QueryScopeError: ErrorSpec(
        code="query_scope_invalid",
        status=422,
        message="The query scope is not valid for this request.",
        # Validation of the caller's own request scope.
        expose_message=True,
    ),
    EgressUrlError: ErrorSpec(
        code="egress_url_invalid",
        status=400,
        message="The provider base URL is not valid.",
        # Validation of a URL the tenant admin just submitted.
        expose_message=True,
    ),
    UploadTooLargeError: ErrorSpec(
        code="upload_too_large",
        status=413,
        message=(
            f"Document size exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB "
            "ingestion budget limit."
        ),
    ),
    CorpusManifestError: ErrorSpec(
        code="corpus_manifest_invalid",
        status=400,
        message="The corpus manifest is not valid.",
        # May embed server-side file paths.
    ),
    IngestionError: ErrorSpec(
        code="ingestion_failed",
        status=500,
        message="Ingestion failed. The failure has been recorded.",
        # Wraps cross-store write failures; can contain connection detail.
    ),
    SecretEncryptionError: ErrorSpec(
        code="secret_unavailable",
        status=503,
        message="A stored credential could not be read.",
        # Crypto and key-management internals.
    ),
}


def spec_for(exc: BaseException) -> ErrorSpec | None:
    """Return the spec registered for ``exc``'s type, walking its MRO.

    Walking the MRO means a subclass inherits its parent's presentation
    without needing its own entry — ``IngestionCompensationError`` resolves to
    ``IngestionError``. Returns None when nothing is registered, which is the
    signal to fall through to the unhandled path.
    """
    for klass in type(exc).__mro__:
        spec = ERROR_MAP.get(klass)
        if spec is not None:
            return spec
    return None


def detail_for(exc: BaseException, spec: ErrorSpec) -> str:
    """The string that goes into the response body. Never unconditionally str(exc)."""
    if spec.expose_message:
        text = str(exc)
        if text:
            return text
    return spec.message


# --- appended to src/securgraph/api/errors.py ---

_LOG = structlog.get_logger(__name__)

# Codes for HTTPExceptions raised by hand across the routes, and by the
# framework itself. Anything not listed falls back to "http_<status>", so a
# new status is never a crash and never an empty code.
CODE_BY_STATUS: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "upload_too_large",
    422: "unprocessable",
    429: "rate_limited",
    451: "egress_blocked",
    503: "service_unavailable",
}

INTERNAL_ERROR_MESSAGE = "The request could not be completed."


def request_id_for(request: Request | None) -> str | None:
    """Read the correlation id, preferring the request over the contextvar.

    Starlette's ServerErrorMiddleware — the one that runs the ``Exception``
    handler — sits OUTSIDE the user middleware stack. By the time it calls a
    handler, RequestIdMiddleware's ``finally`` has already reset the
    contextvar, so ``get_request_id()`` returns None on exactly the path a
    user is most likely to report. ``request.state`` is bound to the same
    scope and survives. Verified by probe: contextvar None, state present.
    """
    if request is not None:
        value = getattr(request.state, "request_id", None)
        if isinstance(value, str) and value:
            return value
    # No request to read: the caller is inside the route, where the contextvar
    # is still bound.
    return get_request_id()


def error_body(request: Request | None, detail: Any, code: str) -> dict[str, Any]:
    """The one response shape for every error path.

    ``detail`` is passed through with its own type. A route may raise
    ``HTTPException`` with a structured detail — ``settings.py:496`` returns
    ``{"message": ..., "references": [...]}`` on a 409 — and coercing that with
    ``str()`` would hand the caller a Python repr instead of JSON.
    """
    return {"detail": detail, "code": code, "request_id": request_id_for(request)}


def _headers_for(request_id: str | None) -> dict[str, str] | None:
    """Echo the id on error responses too.

    RequestIdMiddleware sets this header on its way out, but a response built
    by ServerErrorMiddleware never passes back through it. The handlers set it
    themselves so every response carries the id, not just the successful ones.
    """
    return {REQUEST_ID_HEADER: request_id} if request_id else None


def code_for_status(status_code: int) -> str:
    return CODE_BY_STATUS.get(status_code, f"http_{status_code}")


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Registered exceptions: status and code from the registry."""
    spec = spec_for(exc)
    if spec is None:  # pragma: no cover - registration guarantees a spec
        return await unhandled_handler(request, exc)
    if spec.status >= 500:
        _LOG.error("request_failed", code=spec.code, exc_info=exc)
    body = error_body(request, detail_for(exc, spec), spec.code)
    return JSONResponse(
        status_code=spec.status,
        content=body,
        headers=_headers_for(body["request_id"]),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """HTTPExceptions keep their detail and gain a code derived from status."""
    body = error_body(request, exc.detail, code_for_status(exc.status_code))
    headers = dict(exc.headers or {})
    if body["request_id"]:
        headers[REQUEST_ID_HEADER] = body["request_id"]
    return JSONResponse(
        status_code=exc.status_code,
        content=body,
        headers=headers or None,
    )


async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """Anything unregistered. The exception text goes to the log, never the body.

    This is the handler that closes the leak: two catch-all branches used to
    interpolate str(exc) into `detail`, and str() of a SQLAlchemy error carries
    the statement and often its bound parameters.
    """
    _LOG.error("unhandled_exception", exc_info=exc)
    body = error_body(request, INTERNAL_ERROR_MESSAGE, "internal_error")
    return JSONResponse(
        status_code=500,
        content=body,
        headers=_headers_for(body["request_id"]),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the three handlers.

    Registration order does not matter: Starlette resolves a handler by
    walking the raised exception's MRO against the handler dict, and lifts
    the ``Exception`` key out to ``ServerErrorMiddleware`` when it builds the
    stack. FastAPI's own ``RequestValidationError`` handler is untouched and
    still wins for 422s, because its class is found before ``Exception`` is
    consulted.
    """
    for exc_type in ERROR_MAP:
        app.add_exception_handler(exc_type, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_handler)
