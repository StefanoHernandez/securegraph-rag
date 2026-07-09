"""Unit tests for startup secret-hygiene guards (P0.4)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from securgraph.api.main import enforce_secret_hygiene
from securgraph.config import DEFAULT_JWT_SECRET, Settings

pytestmark = pytest.mark.unit


def _settings(env: str, jwt_secret: str) -> Settings:
    # jwt_secret has validation_alias="JWT_SECRET", so the constructor kwarg
    # must use the alias — the field name would be silently ignored.
    return Settings(env=env, JWT_SECRET=SecretStr(jwt_secret))


def test_prod_with_default_jwt_secret_refuses_startup() -> None:
    with pytest.raises(RuntimeError, match="known-public default"):
        enforce_secret_hygiene(_settings("prod", DEFAULT_JWT_SECRET))


def test_stage_with_short_jwt_secret_refuses_startup() -> None:
    with pytest.raises(RuntimeError, match="too short"):
        enforce_secret_hygiene(_settings("stage", "short-secret"))


def test_prod_with_strong_jwt_secret_passes() -> None:
    enforce_secret_hygiene(_settings("prod", "x" * 48))


def test_dev_with_default_jwt_secret_allowed() -> None:
    enforce_secret_hygiene(_settings("dev", DEFAULT_JWT_SECRET))


def test_test_env_with_default_jwt_secret_allowed() -> None:
    enforce_secret_hygiene(_settings("test", DEFAULT_JWT_SECRET))


def test_master_key_too_short_raises() -> None:
    from unittest.mock import patch

    from securgraph.security.secrets import SecretEncryptionError, _master_key_bytes

    short = Settings(master_key=SecretStr("abc"))
    with (
        patch("securgraph.security.secrets.get_settings", return_value=short),
        pytest.raises(SecretEncryptionError, match="too short"),
    ):
        _master_key_bytes()


def test_master_key_valid_returns_32_bytes() -> None:
    from unittest.mock import patch

    from securgraph.security.secrets import _master_key_bytes

    ok = Settings(master_key=SecretStr("a-sufficiently-long-master-key"))
    with patch("securgraph.security.secrets.get_settings", return_value=ok):
        assert len(_master_key_bytes()) == 32


def _mismatched_settings(env: str) -> Settings:
    # Explicit kwargs: Settings reads .env, whose overrides would otherwise
    # mask the ollama-shaped defaults this guard exists to catch.
    return Settings(
        env=env,
        llm_provider="mistral",
        llm_model="gemma3:1b",
        llm_model_extract="mistral-small-latest",
        llm_provider_judge="openai",
        llm_model_judge="gpt-4o-mini",
    )


def test_cloud_provider_with_ollama_model_warns_in_dev() -> None:
    from securgraph.api.main import check_provider_model_coherence

    problems = check_provider_model_coherence(_mismatched_settings("dev"))
    assert len(problems) == 1
    assert "gemma3:1b" in problems[0]


def test_cloud_provider_with_ollama_model_refuses_in_prod() -> None:
    from securgraph.api.main import check_provider_model_coherence

    with pytest.raises(RuntimeError, match="gemma3:1b"):
        check_provider_model_coherence(_mismatched_settings("prod"))


def test_cloud_provider_with_cloud_models_passes() -> None:
    from securgraph.api.main import check_provider_model_coherence

    settings = Settings(
        env="prod",
        llm_provider="mistral",
        llm_model="mistral-small-latest",
        llm_model_extract="mistral-small-latest",
        llm_provider_judge="openai",
        llm_model_judge="gpt-4o-mini",
    )
    assert check_provider_model_coherence(settings) == []


def test_ollama_provider_with_ollama_model_passes() -> None:
    from securgraph.api.main import check_provider_model_coherence

    settings = Settings(env="prod", llm_provider="ollama")
    assert check_provider_model_coherence(settings) == []
