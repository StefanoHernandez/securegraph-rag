"""The credential wiring for Qdrant and OpenFGA clients.

These factories exist because the credential was previously absent from eight
separate construction sites. A regression here is silent: the client still
builds, the request still goes out, and it only fails once the datastore is
actually configured to require authentication.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from securgraph.adapters.openfga import build_openfga_configuration
from securgraph.adapters.qdrant import build_qdrant_client
from securgraph.config import Settings

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, env="test", **overrides)


def test_qdrant_client_carries_the_configured_api_key() -> None:
    client = build_qdrant_client(_settings(qdrant_api_key=SecretStr("qdrant-secret")))
    try:
        assert client._client._api_key == "qdrant-secret"
    finally:
        client.close()


def test_qdrant_client_omits_an_empty_api_key() -> None:
    """A local unauthenticated dev instance must still work."""
    client = build_qdrant_client(_settings(qdrant_api_key=SecretStr("")))
    try:
        assert client._client._api_key is None
    finally:
        client.close()


def test_qdrant_client_forwards_extra_kwargs() -> None:
    client = build_qdrant_client(_settings(), check_compatibility=False)
    client.close()


def test_openfga_configuration_carries_the_preshared_token() -> None:
    config = build_openfga_configuration(
        _settings(openfga_api_token=SecretStr("fga-secret"))
    )
    assert config.credentials is not None
    assert config.credentials.method == "api_token"
    assert config.credentials.configuration.api_token == "fga-secret"


def test_openfga_configuration_omits_empty_token() -> None:
    config = build_openfga_configuration(_settings(openfga_api_token=SecretStr("")))
    assert config.credentials is None


def test_openfga_configuration_preserves_store_and_model_ids() -> None:
    config = build_openfga_configuration(
        _settings(
            openfga_store_id="store-1",
            openfga_authorization_model_id="model-1",
            openfga_api_token=SecretStr("fga-secret"),
        )
    )
    assert config.store_id == "store-1"
    assert config.authorization_model_id == "model-1"
