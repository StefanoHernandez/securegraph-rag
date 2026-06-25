"""HTTP security middleware shared by every API route."""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from securgraph.api.routes.auth import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT_SUFFIXES = ("/auth/login",)


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
