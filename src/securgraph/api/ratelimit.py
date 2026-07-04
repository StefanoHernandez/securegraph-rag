"""In-process sliding-window rate limiter.

Deliberately dependency-free: the documented deployment is single-worker
uvicorn (see ``securgraph.runtime_overrides``), so per-process counters are
the whole picture. If the deployment ever moves to multiple workers, this
module must be replaced with a shared store (Redis) — the call sites won't
change.

Disabled entirely when ``settings.env == "test"`` so the unit suite is not
order-dependent, and per-limiter when its configured limit is 0.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException

from securgraph.config import get_settings

_lock = threading.Lock()
_windows: dict[tuple[str, str], deque[float]] = {}

# Windows are per-minute everywhere; kept as a constant so tests and call
# sites agree.
WINDOW_SECONDS = 60.0


def check_rate(scope: str, key: str, limit: int, window_s: float = WINDOW_SECONDS) -> bool:
    """Record one attempt for ``(scope, key)`` and report whether it fits.

    Returns True when the attempt is within ``limit`` events per ``window_s``
    seconds, False when the caller should be rejected. Thread-safe.
    """
    if limit <= 0 or get_settings().env == "test":
        return True
    now = time.monotonic()
    with _lock:
        window = _windows.setdefault((scope, key), deque())
        cutoff = now - window_s
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True


def reset_rate(scope: str, key: str) -> None:
    """Clear the window for ``(scope, key)`` — e.g. after a successful login."""
    with _lock:
        _windows.pop((scope, key), None)


def reset_all() -> None:
    """Clear every window. Test helper."""
    with _lock:
        _windows.clear()


def enforce_rate(scope: str, key: str, limit: int, window_s: float = WINDOW_SECONDS) -> None:
    """Raise HTTP 429 when ``(scope, key)`` exceeds ``limit`` per window."""
    if not check_rate(scope, key, limit, window_s):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Retry later.",
            headers={"Retry-After": str(int(window_s))},
        )
