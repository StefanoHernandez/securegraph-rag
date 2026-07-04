"""Unit tests for the in-process sliding-window rate limiter (P0.2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from securgraph.api import ratelimit
from securgraph.api.ratelimit import check_rate, enforce_rate, reset_all, reset_rate

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_windows():
    reset_all()
    yield
    reset_all()


@pytest.fixture
def env_dev():
    """The limiter is disabled in env=test (the suite default) — force dev."""
    settings = MagicMock()
    settings.env = "dev"
    with patch.object(ratelimit, "get_settings", return_value=settings):
        yield


def test_within_limit_allows(env_dev) -> None:
    assert all(check_rate("s", "k", limit=3) for _ in range(3))


def test_over_limit_blocks(env_dev) -> None:
    for _ in range(3):
        check_rate("s", "k", limit=3)
    assert check_rate("s", "k", limit=3) is False


def test_keys_are_independent(env_dev) -> None:
    for _ in range(3):
        check_rate("s", "alice", limit=3)
    assert check_rate("s", "alice", limit=3) is False
    assert check_rate("s", "bob", limit=3) is True


def test_window_expiry_frees_slots(env_dev) -> None:
    base = 1000.0
    with patch.object(ratelimit.time, "monotonic", return_value=base):
        for _ in range(3):
            check_rate("s", "k", limit=3, window_s=60.0)
        assert check_rate("s", "k", limit=3, window_s=60.0) is False
    with patch.object(ratelimit.time, "monotonic", return_value=base + 61.0):
        assert check_rate("s", "k", limit=3, window_s=60.0) is True


def test_reset_clears_counter(env_dev) -> None:
    for _ in range(3):
        check_rate("s", "k", limit=3)
    reset_rate("s", "k")
    assert check_rate("s", "k", limit=3) is True


def test_enforce_raises_429(env_dev) -> None:
    for _ in range(2):
        enforce_rate("s", "k", limit=2)
    with pytest.raises(HTTPException) as exc:
        enforce_rate("s", "k", limit=2)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "60"


def test_zero_limit_disables(env_dev) -> None:
    assert all(check_rate("s", "k", limit=0) for _ in range(100))


def test_test_env_disables() -> None:
    # Suite runs with SECURGRAPH_ENV=test: never rate-limited.
    assert all(check_rate("s", "k", limit=1) for _ in range(10))
