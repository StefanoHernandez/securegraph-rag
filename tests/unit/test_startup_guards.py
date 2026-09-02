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


def test_prod_with_auth_disabled_refuses_startup() -> None:
    from securgraph.api.main import enforce_auth_hygiene

    with pytest.raises(RuntimeError, match="AUTH_ENABLED=false"):
        enforce_auth_hygiene(Settings(env="prod", auth_enabled=False))


def test_prod_requires_workers_for_durable_ingestion_saga() -> None:
    from securgraph.api.main import enforce_worker_hygiene

    with pytest.raises(RuntimeError, match="FEATURE_WORKERS_ENABLED"):
        enforce_worker_hygiene(Settings(env="prod", feature_workers_enabled=False))


def test_dev_allows_synchronous_ingestion_without_workers() -> None:
    from securgraph.api.main import enforce_worker_hygiene

    enforce_worker_hygiene(Settings(env="dev", feature_workers_enabled=False))


def test_stage_with_auth_disabled_refuses_startup() -> None:
    from securgraph.api.main import enforce_auth_hygiene

    with pytest.raises(RuntimeError, match="AUTH_ENABLED=false"):
        enforce_auth_hygiene(Settings(env="stage", auth_enabled=False))


def test_dev_with_auth_disabled_allowed() -> None:
    from securgraph.api.main import enforce_auth_hygiene

    enforce_auth_hygiene(Settings(env="dev", auth_enabled=False))


def test_test_env_with_auth_disabled_allowed() -> None:
    from securgraph.api.main import enforce_auth_hygiene

    enforce_auth_hygiene(Settings(env="test", auth_enabled=False))


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


def test_ollama_provider_refuses_startup_in_prod() -> None:
    from securgraph.api.main import check_provider_model_coherence

    settings = Settings(env="prod", llm_provider="ollama")
    with pytest.raises(RuntimeError, match="Ollama"):
        check_provider_model_coherence(settings)


def test_ollama_provider_warns_in_dev() -> None:
    from securgraph.api.main import check_provider_model_coherence

    settings = Settings(
        env="dev",
        llm_provider="ollama",
        llm_provider_extract="ollama",
        llm_provider_judge="ollama",
    )
    problems = check_provider_model_coherence(settings)
    assert len(problems) == 3
    assert all("Ollama" in problem for problem in problems)


def _production_audit_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "prod",
        "audit_tamper_evident": True,
        "AUDIT_HMAC_KEY": SecretStr("ab" * 32),
        "audit_anchor_bucket": "securegraph-audit-anchors",
        "audit_anchor_endpoint": "https://minio.audit.example",
        "audit_anchor_access_key": "anchor-writer",
        "audit_anchor_secret_key": SecretStr("secret-value"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_prod_requires_tamper_evident_audit() -> None:
    from securgraph.api.main import enforce_audit_hygiene

    with pytest.raises(RuntimeError, match="AUDIT_TAMPER_EVIDENT"):
        enforce_audit_hygiene(_production_audit_settings(audit_tamper_evident=False))


def test_prod_requires_hmac_key() -> None:
    from securgraph.api.main import enforce_audit_hygiene

    with pytest.raises(RuntimeError, match="HMAC"):
        enforce_audit_hygiene(_production_audit_settings(AUDIT_HMAC_KEY=SecretStr("")))


def test_prod_requires_minio_object_lock_configuration() -> None:
    from securgraph.api.main import enforce_audit_hygiene

    with pytest.raises(RuntimeError, match="MinIO Object Lock"):
        enforce_audit_hygiene(_production_audit_settings(audit_anchor_bucket=""))


def test_prod_accepts_complete_minio_object_lock_configuration() -> None:
    from securgraph.api.main import enforce_audit_hygiene

    enforce_audit_hygiene(_production_audit_settings())


def test_prod_rejects_aws_endpoint_for_minio_object_lock() -> None:
    from securgraph.api.main import enforce_audit_hygiene

    with pytest.raises(RuntimeError, match="MinIO Object Lock"):
        enforce_audit_hygiene(
            _production_audit_settings(
                audit_anchor_endpoint="https://securegraph-audit.s3.amazonaws.com"
            )
        )


def test_legacy_s3_environment_names_map_to_minio_fields() -> None:
    settings = Settings(
        _env_file=None,
        audit_anchor_s3_bucket="legacy-bucket",
        audit_anchor_s3_endpoint="https://minio.example",
        audit_anchor_s3_access_key="legacy-user",
        audit_anchor_s3_secret_key=SecretStr("legacy-secret"),
    )

    assert settings.audit_anchor_bucket == "legacy-bucket"
    assert settings.audit_anchor_endpoint == "https://minio.example"
    assert settings.audit_anchor_backend == "minio"


def test_canonical_minio_fields_are_the_settings_contract() -> None:
    settings = Settings(
        _env_file=None,
        audit_anchor_bucket="securegraph-audit-anchors",
        audit_anchor_endpoint="https://minio.example",
        audit_anchor_access_key="anchor-writer",
        audit_anchor_secret_key=SecretStr("anchor-secret"),
    )

    assert settings.audit_anchor_bucket == "securegraph-audit-anchors"
    assert settings.audit_anchor_endpoint == "https://minio.example"
    assert settings.audit_anchor_backend == "minio"


# ---------------------------------------------------------------------------
# Datastore hygiene: the app-layer guarantees (OpenFGA ReBAC, PostgreSQL RLS)
# are only worth anything if the datastores themselves refuse anonymous
# callers. These guards keep a stage/prod deployment from booting against the
# unauthenticated local dev stack.
# ---------------------------------------------------------------------------

DEFAULT_NEO4J_PASSWORD = "securgraph-neo4j"


def _hardened_datastore_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "prod",
        "qdrant_api_key": SecretStr("qdrant-" + "k" * 32),
        "openfga_api_token": SecretStr("fga-" + "t" * 32),
        "neo4j_password": SecretStr("rotated-neo4j-" + "p" * 20),
        "postgres_dsn": "postgresql+asyncpg://app:s3cr3t-rotated@db.internal:5432/securgraph",
        "redis_url": "redis://:redis-secret@redis.internal:6379/0",
        "feature_workers_enabled": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_prod_requires_openfga_api_token() -> None:
    """OpenFGA is the C1 ground truth; an open API means anyone self-authorizes."""
    from securgraph.api.main import enforce_datastore_hygiene

    with pytest.raises(RuntimeError, match="OPENFGA_API_TOKEN"):
        enforce_datastore_hygiene(
            _hardened_datastore_settings(openfga_api_token=SecretStr(""))
        )


def test_prod_requires_qdrant_api_key() -> None:
    """An open Qdrant exposes every chunk payload, bypassing OpenFGA entirely."""
    from securgraph.api.main import enforce_datastore_hygiene

    with pytest.raises(RuntimeError, match="QDRANT_API_KEY"):
        enforce_datastore_hygiene(_hardened_datastore_settings(qdrant_api_key=SecretStr("")))


def test_prod_refuses_default_neo4j_password() -> None:
    from securgraph.api.main import enforce_datastore_hygiene

    with pytest.raises(RuntimeError, match="NEO4J_PASSWORD"):
        enforce_datastore_hygiene(
            _hardened_datastore_settings(neo4j_password=SecretStr(DEFAULT_NEO4J_PASSWORD))
        )


def test_prod_refuses_default_postgres_credentials() -> None:
    from securgraph.api.main import enforce_datastore_hygiene

    with pytest.raises(RuntimeError, match="POSTGRES_DSN"):
        enforce_datastore_hygiene(
            _hardened_datastore_settings(
                postgres_dsn=(
                    "postgresql+asyncpg://securgraph_app:securgraph_app"
                    "@localhost:5432/securgraph"
                )
            )
        )


def test_prod_requires_redis_password_when_workers_enabled() -> None:
    from securgraph.api.main import enforce_datastore_hygiene

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        enforce_datastore_hygiene(
            _hardened_datastore_settings(redis_url="redis://redis.internal:6379/0")
        )


def test_prod_with_hardened_datastores_passes() -> None:
    from securgraph.api.main import enforce_datastore_hygiene

    enforce_datastore_hygiene(_hardened_datastore_settings())


def test_dev_tolerates_unauthenticated_datastores() -> None:
    """The local compose stack must stay frictionless in dev and test."""
    from securgraph.api.main import enforce_datastore_hygiene

    for env in ("dev", "test"):
        enforce_datastore_hygiene(Settings(_env_file=None, env=env))


def test_prod_refuses_weak_master_key() -> None:
    """The master key is the KEK for every stored provider credential."""
    with pytest.raises(RuntimeError, match="MASTER_KEY"):
        enforce_secret_hygiene(
            Settings(
                _env_file=None,
                env="prod",
                JWT_SECRET=SecretStr("x" * 48),
                master_key=SecretStr("short-passphrase"),
            )
        )


def test_prod_with_strong_master_key_passes() -> None:
    enforce_secret_hygiene(
        Settings(
            _env_file=None,
            env="prod",
            JWT_SECRET=SecretStr("x" * 48),
            master_key=SecretStr("m" * 32),
        )
    )
