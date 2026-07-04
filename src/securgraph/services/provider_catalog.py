"""Provider credential validation and model catalog retrieval."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

ProviderName = Literal["openai", "anthropic", "gemini", "mistral", "ollama"]


@dataclass(frozen=True, slots=True)
class ProviderModel:
    provider: ProviderName
    model_id: str
    capabilities: list[str]


DEFAULT_BASE_URLS: dict[ProviderName, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "mistral": "https://api.mistral.ai/v1",
    "ollama": "http://localhost:11434",
}


def default_provider_base_url(provider: ProviderName) -> str:
    return DEFAULT_BASE_URLS[provider]


class EgressUrlError(ValueError):
    """Raised when an admin-supplied base_url must not be fetched (SSRF guard)."""


# Providers that legitimately run on the operator's own machine.
_LOCAL_ALLOWED_PROVIDERS = frozenset({"ollama"})


def _is_forbidden_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_egress_url(url: str, *, provider: str | None = None) -> None:
    """Reject base URLs that would let an admin make the server fetch
    internal endpoints (cloud metadata, RFC-1918 hosts, loopback services).

    Local providers (``ollama``) are exempt: running them on localhost is the
    normal deployment. Raises :class:`EgressUrlError` with a clear reason.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise EgressUrlError(f"URL scheme '{parsed.scheme}' is not allowed (use http/https)")
    hostname = parsed.hostname
    if not hostname:
        raise EgressUrlError("URL has no hostname")
    if provider in _LOCAL_ALLOWED_PROVIDERS:
        return
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise EgressUrlError(f"Cannot resolve host '{hostname}'") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if _is_forbidden_address(addr):
            raise EgressUrlError(
                f"Host '{hostname}' resolves to a private or reserved address "
                f"({addr}); refusing to fetch it"
            )


async def fetch_provider_models(
    provider: ProviderName,
    *,
    api_key: str | None,
    base_url: str | None = None,
    timeout_s: float = 10.0,
) -> list[ProviderModel]:
    """Fetch the official model catalog endpoint for a provider."""

    origin = (base_url or default_provider_base_url(provider)).rstrip("/")
    validate_egress_url(origin, provider=provider)
    headers: dict[str, str] = {}
    url = f"{origin}/models"
    params: dict[str, str] | None = None

    if provider in {"openai", "mistral"}:
        if not api_key:
            raise ValueError("API key is required")
        headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "anthropic":
        if not api_key:
            raise ValueError("API key is required")
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "gemini":
        if not api_key:
            raise ValueError("API key is required")
        params = {"key": api_key}
    elif provider == "ollama":
        url = f"{origin}/api/tags"

    # follow_redirects stays False: a public host could otherwise 302 the
    # request to an internal address after passing validation.
    async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        payload = response.json()

    return _parse_models(provider, payload)


def _parse_models(provider: ProviderName, payload: dict[str, Any]) -> list[ProviderModel]:
    if provider == "ollama":
        items = payload.get("models", [])
        return [
            ProviderModel(provider=provider, model_id=str(item["name"]), capabilities=["chat"])
            for item in items
            if isinstance(item, dict) and item.get("name")
        ]
    items = payload.get("data") or payload.get("models") or []
    models: list[ProviderModel] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name")
        if model_id:
            models.append(
                ProviderModel(provider=provider, model_id=str(model_id), capabilities=["chat"])
            )
    return models
