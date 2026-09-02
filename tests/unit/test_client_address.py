"""Resolving the real client address behind the BFF.

The API sits behind the Next.js BFF, so ``request.client.host`` is the proxy
on every request. Two things break as a result: the per-IP login limiter
becomes one global bucket that any single attacker can exhaust for everybody,
and the ip_address recorded on each session — surfaced by GET /auth/sessions
so a user can spot a session that is not theirs — is the same value for all
of them.

Trusting X-Forwarded-For to fix that would be worse than the bug: an attacker
who can set the header picks a fresh address per request and is never limited
at all. So the header counts only when the immediate peer is a configured
trusted proxy, and the address is taken by walking the chain from the right
and skipping trusted hops — the first untrusted entry is the closest thing to
the client that a trusted proxy actually observed.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from securgraph.api.client_address import client_ip
from securgraph.config import Settings

pytestmark = pytest.mark.unit


def _request(peer: str | None, **headers: str) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": [
            (key.replace("_", "-").encode(), value.encode()) for key, value in headers.items()
        ],
        "client": (peer, 54321) if peer is not None else None,
    }
    return Request(scope)


def _settings(*trusted: str) -> Settings:
    return Settings(_env_file=None, env="test", trusted_proxies=list(trusted))


def test_without_trusted_proxies_the_peer_wins() -> None:
    """Default configuration must ignore the header entirely."""
    request = _request("10.0.0.9", x_forwarded_for="203.0.113.7")
    assert client_ip(request, settings=_settings()) == "10.0.0.9"


def test_untrusted_peer_cannot_spoof_the_header() -> None:
    """A direct caller claiming a forwarded address is not believed."""
    request = _request("198.51.100.4", x_forwarded_for="203.0.113.7")
    assert client_ip(request, settings=_settings("127.0.0.1")) == "198.51.100.4"


def test_trusted_peer_forwards_the_client_address() -> None:
    request = _request("127.0.0.1", x_forwarded_for="203.0.113.7")
    assert client_ip(request, settings=_settings("127.0.0.1")) == "203.0.113.7"


def test_trailing_trusted_hops_are_skipped() -> None:
    """Rightmost-first: the last untrusted entry is the observed client."""
    request = _request(
        "127.0.0.1",
        x_forwarded_for="203.0.113.7, 10.1.2.3, 127.0.0.1",
    )
    resolved = client_ip(request, settings=_settings("127.0.0.1", "10.1.2.0/24"))
    assert resolved == "203.0.113.7"


def test_attacker_prepended_entries_are_not_reached() -> None:
    """Values a client injects sit to the LEFT of what the proxy observed.

    The browser sends `X-Forwarded-For: 1.2.3.4`; the trusted proxy appends
    the address it actually saw. Walking from the right stops at that
    observed address and never reaches the fabricated one.
    """
    request = _request("127.0.0.1", x_forwarded_for="1.2.3.4, 203.0.113.7")
    assert client_ip(request, settings=_settings("127.0.0.1")) == "203.0.113.7"


def test_all_hops_trusted_falls_back_to_the_peer() -> None:
    request = _request("127.0.0.1", x_forwarded_for="127.0.0.1, 127.0.0.1")
    assert client_ip(request, settings=_settings("127.0.0.1")) == "127.0.0.1"


def test_malformed_header_falls_back_to_the_peer() -> None:
    """A bad value must not become a rate-limit key of its own."""
    request = _request("127.0.0.1", x_forwarded_for="not-an-ip")
    assert client_ip(request, settings=_settings("127.0.0.1")) == "127.0.0.1"


def test_cidr_ranges_are_supported() -> None:
    request = _request("10.4.0.17", x_forwarded_for="203.0.113.7")
    assert client_ip(request, settings=_settings("10.4.0.0/16")) == "203.0.113.7"


def test_ipv6_peer_and_forwarded_address() -> None:
    request = _request("::1", x_forwarded_for="2001:db8::dead")
    assert client_ip(request, settings=_settings("::1")) == "2001:db8::dead"


def test_missing_client_is_reported_as_unknown() -> None:
    assert client_ip(_request(None), settings=_settings()) == "unknown"


def test_invalid_trusted_proxy_entry_is_ignored_not_fatal() -> None:
    """A typo in configuration must not make every caller trusted."""
    request = _request("127.0.0.1", x_forwarded_for="203.0.113.7")
    assert client_ip(request, settings=_settings("nonsense")) == "127.0.0.1"


# ---------------------------------------------------------------------------
# The property that matters at the login endpoint
# ---------------------------------------------------------------------------


def test_distinct_clients_behind_the_proxy_get_distinct_keys() -> None:
    """Two users behind the BFF must not share one rate-limit bucket.

    This is the whole point: with request.client.host the key was the proxy's
    address for everyone, so ratelimit_login_ip=10/min was a single global
    budget that one attacker could exhaust for the entire deployment.
    """
    settings = _settings("127.0.0.1")
    first = _request("127.0.0.1", x_forwarded_for="203.0.113.7")
    second = _request("127.0.0.1", x_forwarded_for="203.0.113.8")

    assert client_ip(first, settings=settings) != client_ip(second, settings=settings)


def test_one_client_cannot_split_itself_across_buckets() -> None:
    """The other half: a spoofed header must not hand out fresh budgets.

    Without a trusted peer the claimed address is discarded, so every attempt
    from one caller lands on the same key however it decorates the request.
    """
    settings = _settings("127.0.0.1")
    keys = {
        client_ip(_request("198.51.100.4", x_forwarded_for=claimed), settings=settings)
        for claimed in ("203.0.113.1", "203.0.113.2", "203.0.113.3")
    }
    assert keys == {"198.51.100.4"}
