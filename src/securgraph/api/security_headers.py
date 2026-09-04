"""Baseline security headers, in a module both middleware and handlers import.

`SecurityHeadersMiddleware` applies these to every response that passes back
through the user middleware stack. Unhandled 500s do not: Starlette lifts the
``Exception`` handler out to `ServerErrorMiddleware`, which sits outside that
stack, so its response is written past every middleware. The unhandled handler
therefore applies the same headers itself, and this module exists so it can do
that without importing `api.middleware`, which imports `api.errors`.

The API answers JSON, so it needs a narrower set than the web app: no CSP,
because the interactive docs load Swagger UI from a CDN and a strict policy
would break them. These cover what still applies to a JSON endpoint a browser
reaches through the BFF.
"""

from __future__ import annotations

BASELINE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
}
