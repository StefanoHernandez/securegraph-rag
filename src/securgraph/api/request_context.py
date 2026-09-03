"""Per-request correlation id.

Kept in a ContextVar rather than only on ``request.state`` so that exception
handlers, the logging processor chain, and any code far from the request
object all read the same value. ``structlog.contextvars.merge_contextvars`` is
already in the processor chain (``securgraph/logging.py:38``), so binding the
id there puts it on every log line emitted during the request at no extra
cost.
"""

from __future__ import annotations

from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str | None] = ContextVar("securgraph_request_id", default=None)


def set_request_id(value: str) -> object:
    """Bind the id for the current context. Returns the reset token."""
    return _request_id.set(value)


def reset_request_id(token: object) -> None:
    _request_id.reset(token)  # type: ignore[arg-type]


def get_request_id() -> str | None:
    return _request_id.get()
