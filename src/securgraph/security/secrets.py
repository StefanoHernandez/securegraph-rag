"""Envelope encryption helpers for database-stored provider credentials."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from securgraph.config import get_settings

SECRET_PREFIX = "v1"  # noqa: S105 - version marker, not a credential.


class SecretEncryptionError(RuntimeError):
    """Raised when secret encryption is unavailable or ciphertext is invalid."""


def _master_key_bytes() -> bytes:
    value = get_settings().master_key.get_secret_value()
    if not value:
        raise SecretEncryptionError("SECURGRAPH_MASTER_KEY is required for credential storage")
    return hashlib.sha256(value.encode()).digest()


def encrypt_secret(secret: str, *, associated_data: str) -> bytes:
    """Encrypt a secret with AES-GCM using the external master key."""

    return encrypt_bytes(secret.encode(), associated_data=associated_data)


def encrypt_bytes(data: bytes, *, associated_data: str) -> bytes:
    """Encrypt arbitrary bytes with AES-GCM using the external master key."""

    nonce = os.urandom(12)
    ciphertext = AESGCM(_master_key_bytes()).encrypt(
        nonce,
        data,
        associated_data.encode(),
    )
    payload = ":".join(
        (
            SECRET_PREFIX,
            base64.urlsafe_b64encode(nonce).decode(),
            base64.urlsafe_b64encode(ciphertext).decode(),
        )
    )
    return payload.encode()


def decrypt_secret(payload: bytes, *, associated_data: str) -> str:
    """Decrypt an AES-GCM payload created by :func:`encrypt_secret`."""

    try:
        return decrypt_bytes(payload, associated_data=associated_data).decode()
    except UnicodeDecodeError as exc:
        raise SecretEncryptionError("Invalid encrypted secret payload") from exc


def decrypt_bytes(payload: bytes, *, associated_data: str) -> bytes:
    """Decrypt arbitrary bytes created by :func:`encrypt_bytes`."""

    try:
        version, nonce_b64, ciphertext_b64 = payload.decode().split(":", maxsplit=2)
        if version != SECRET_PREFIX:
            raise SecretEncryptionError("Unsupported secret payload version")
        return AESGCM(_master_key_bytes()).decrypt(
            base64.urlsafe_b64decode(nonce_b64.encode()),
            base64.urlsafe_b64decode(ciphertext_b64.encode()),
            associated_data.encode(),
        )
    except SecretEncryptionError:
        raise
    except Exception as exc:
        raise SecretEncryptionError("Invalid encrypted secret payload") from exc


def secret_fingerprint(secret: str) -> str:
    """Return a non-reversible fingerprint suitable for display and audit."""

    return hashlib.sha256(secret.encode()).hexdigest()[:16]
