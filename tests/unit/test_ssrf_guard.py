"""Unit tests for the provider base_url SSRF guard (P0.3)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from securgraph.services.provider_catalog import (
    EgressUrlError,
    fetch_provider_models,
    validate_egress_url,
)

pytestmark = pytest.mark.unit


def _addrinfo(ip: str) -> list[tuple]:
    return [(2, 1, 6, "", (ip, 0))]


def test_metadata_ip_rejected() -> None:
    with pytest.raises(EgressUrlError, match="private or reserved"):
        validate_egress_url("http://169.254.169.254/latest/meta-data")


def test_rfc1918_rejected() -> None:
    with pytest.raises(EgressUrlError, match="private or reserved"):
        validate_egress_url("http://10.0.0.5:8080/v1")


def test_loopback_rejected_for_cloud_provider() -> None:
    with pytest.raises(EgressUrlError, match="private or reserved"):
        validate_egress_url("http://127.0.0.1:8000/v1", provider="openai")


def test_bad_scheme_rejected() -> None:
    with pytest.raises(EgressUrlError, match="scheme"):
        validate_egress_url("file:///etc/passwd")


def test_unresolvable_host_rejected() -> None:
    import socket

    with (
        patch(
            "securgraph.services.provider_catalog.socket.getaddrinfo",
            side_effect=socket.gaierror("nope"),
        ),
        pytest.raises(EgressUrlError, match="Cannot resolve"),
    ):
        validate_egress_url("https://does-not-exist.invalid/v1")


def test_public_host_allowed() -> None:
    with patch(
        "securgraph.services.provider_catalog.socket.getaddrinfo",
        return_value=_addrinfo("104.18.32.1"),
    ):
        validate_egress_url("https://api.example.com/v1")


def test_dns_rebinding_to_private_rejected() -> None:
    # Public-looking hostname that resolves to a private address must fail.
    with (
        patch(
            "securgraph.services.provider_catalog.socket.getaddrinfo",
            return_value=_addrinfo("192.168.1.10"),
        ),
        pytest.raises(EgressUrlError, match="private or reserved"),
    ):
        validate_egress_url("https://innocent-looking.example.com/v1")


def test_ollama_localhost_allowed() -> None:
    validate_egress_url("http://localhost:11434", provider="ollama")
    validate_egress_url("http://127.0.0.1:11434", provider="ollama")


async def test_fetch_provider_models_validates_before_fetch() -> None:
    with pytest.raises(EgressUrlError):
        await fetch_provider_models(
            "openai",
            api_key="sk-test",
            base_url="http://169.254.169.254",
        )
