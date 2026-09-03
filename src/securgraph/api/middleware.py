"""HTTP security middleware shared by every API route."""

from __future__ import annotations

import hmac
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from securgraph.api.request_context import (
    REQUEST_ID_HEADER,
    reset_request_id,
    set_request_id,
)
from securgraph.api.routes.auth import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT_SUFFIXES = ("/auth/login",)

# The API answers JSON, so it needs a narrower set than the web app: no CSP,
# because the interactive docs load Swagger UI from a CDN and a strict policy
# would break them. These cover what still applies to a JSON endpoint a
# browser reaches through the BFF.
BASELINE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}

# Anything that mints, rotates, or reveals session material. A shared or disk
# cache holding one of these responses hands the next reader a live session.
NO_STORE_PATH_SUFFIXES = ("/auth/login", "/auth/refresh", "/auth/me", "/auth/sessions")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach baseline security headers to every API response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        for header, value in BASELINE_SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.path.endswith(NO_STORE_PATH_SUFFIXES):
            response.headers["Cache-Control"] = "no-store"
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce double-submit CSRF checks for browser cookie sessions.

    Bearer-authenticated API clients do not carry ambient browser credentials
    and therefore do not require a CSRF token.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        cookie_authenticated = bool(
            request.cookies.get(ACCESS_COOKIE) or request.cookies.get(REFRESH_COOKIE)
        )
        bearer_authenticated = request.headers.get("authorization", "").lower().startswith(
            "bearer "
        )
        exempt = request.url.path.endswith(CSRF_EXEMPT_SUFFIXES)
        if (
            request.method in UNSAFE_METHODS
            and cookie_authenticated
            and not bearer_authenticated
            and not exempt
        ):
            cookie_token = request.cookies.get(CSRF_COOKIE, "")
            header_token = request.headers.get("x-csrf-token", "")
            if not cookie_token or not hmac.compare_digest(cookie_token, header_token):
                return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        return await call_next(request)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign every request a correlation id and echo it on the response.

    An inbound ``X-Request-ID`` is ignored rather than honoured: accepting it
    would let a caller fabricate ids or deliberately collide with another
    user's, which is the opposite of what a correlation id is for.

    Registered as the LAST ``add_middleware`` call so it is the outermost
    layer — Starlette's ``add_middleware`` inserts at position 0. Being
    outermost means the id is bound before the CSRF check and before the
    exception handlers, so both can report it.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(uuid.uuid4())
        token = set_request_id(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
            reset_request_id(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
