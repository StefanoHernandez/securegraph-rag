"""OpenFGA client configuration.

Single place where ``ClientConfiguration`` is built, so the preshared API token
from Settings reaches every OpenFGA client (API dependencies, eval seeding).
"""

from __future__ import annotations

from openfga_sdk import ClientConfiguration
from openfga_sdk.credentials import CredentialConfiguration, Credentials

from securgraph.config import Settings, get_settings


def build_openfga_configuration(settings: Settings | None = None) -> ClientConfiguration:
    """Return a ClientConfiguration carrying the preshared API token, if set.

    An empty token yields an unauthenticated client so a local dev OpenFGA
    still works; stage/prod refuse to boot without one.
    """
    resolved = settings or get_settings()
    token = resolved.openfga_api_token.get_secret_value()
    credentials = (
        Credentials(
            method="api_token",
            configuration=CredentialConfiguration(api_token=token),
        )
        if token
        else None
    )
    return ClientConfiguration(
        api_url=resolved.openfga_api_url,
        store_id=resolved.openfga_store_id or None,
        authorization_model_id=resolved.openfga_authorization_model_id or None,
        credentials=credentials,
    )
