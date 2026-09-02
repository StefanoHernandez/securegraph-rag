"""Resolve the address of the real caller behind the BFF.

The API is reached through the Next.js BFF, so ``request.client.host`` is the
proxy's address on every request. That makes the per-IP login limiter a single
global bucket — one attacker exhausts it for every user — and records the same
``ip_address`` on every session row, which ``GET /auth/sessions`` presents to
users so they can spot a login that is not theirs.

Reading ``X-Forwarded-For`` unconditionally would turn that denial of service
into a complete bypass: a caller who sets the header picks a fresh address per
request and is never limited. So the header is honored only when the immediate
peer is a configured trusted proxy, and the address is taken by walking the
chain right-to-left past trusted hops. The first untrusted entry from the
right is the closest address to the client that a trusted proxy actually
observed; anything further left was supplied by the caller and is discarded.

With ``trusted_proxies`` empty — the default — the header is ignored entirely
and the peer address is used, so a misconfigured deployment loses per-client
granularity rather than gaining a spoofable limiter.
"""

from __future__ import annotations

from functools import lru_cache
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.requests import Request

from securgraph.config import Settings, get_settings

UNKNOWN_CLIENT = "unknown"
FORWARDED_FOR_HEADER = "x-forwarded-for"


@lru_cache(maxsize=8)
def _parse_trusted(entries: tuple[str, ...]) -> tuple[IPv4Network | IPv6Network, ...]:
    """Parse configured proxies into networks, dropping unusable entries.

    A typo must not widen trust: an entry that does not parse is skipped, so
    the worst outcome is that a real proxy stops being trusted and callers
    fall back to the peer address.
    """
    networks: list[IPv4Network | IPv6Network] = []
    for entry in entries:
        candidate = entry.strip()
        if not candidate:
            continue
        try:
            networks.append(ip_network(candidate, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_trusted(candidate: str, trusted: tuple[IPv4Network | IPv6Network, ...]) -> bool:
    try:
        address = ip_address(candidate)
    except ValueError:
        return False
    return any(address in network for network in trusted)


def client_ip(request: Request, *, settings: Settings | None = None) -> str:
    """Return the caller's address, or ``"unknown"`` when there is no peer."""
    peer = request.client.host if request.client else None
    if not peer:
        return UNKNOWN_CLIENT

    resolved = settings or get_settings()
    trusted = _parse_trusted(tuple(resolved.trusted_proxies))
    if not trusted or not _is_trusted(peer, trusted):
        return peer

    forwarded = request.headers.get(FORWARDED_FOR_HEADER)
    if not forwarded:
        return peer

    for entry in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
        try:
            address = ip_address(entry)
        except ValueError:
            # A malformed hop means the chain cannot be trusted from here on.
            return peer
        if not _is_trusted(entry, trusted):
            return str(address)
    # Every hop was a trusted proxy: no client address was ever recorded.
    return peer
