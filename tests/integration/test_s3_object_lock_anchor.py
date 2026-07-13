"""Live MinIO Object Lock anchor integration tests.

Verifies the audit Merkle anchor adapter against a self-hosted MinIO bucket
with Object Lock enabled.

Setup (local MinIO):
    docker compose --profile anchor up
    # bucket `securgraph-audit-anchors` is created with Object Lock governance

Then:
    SECURGRAPH_AUDIT_ANCHOR_BUCKET=securgraph-audit-anchors \
    SECURGRAPH_AUDIT_ANCHOR_ENDPOINT=http://localhost:9000 \
    SECURGRAPH_AUDIT_ANCHOR_ACCESS_KEY=minioadmin \
    SECURGRAPH_AUDIT_ANCHOR_SECRET_KEY=minioadmin \
    .venv/bin/pytest tests/integration/test_s3_object_lock_anchor.py -q
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

import pytest

from securgraph.config import get_settings
from securgraph.indexing.anchors import AnchorStorageResult, MinioObjectLockAnchorStorage

pytestmark = pytest.mark.integration

_BUCKET = os.environ.get("SECURGRAPH_AUDIT_ANCHOR_BUCKET", "")
_ENDPOINT = os.environ.get("SECURGRAPH_AUDIT_ANCHOR_ENDPOINT", "")
_ACCESS_KEY = os.environ.get("SECURGRAPH_AUDIT_ANCHOR_ACCESS_KEY", "")
_SECRET_KEY = os.environ.get("SECURGRAPH_AUDIT_ANCHOR_SECRET_KEY", "")

_SKIP_REASON = (
    "Set SECURGRAPH_AUDIT_ANCHOR_BUCKET/ENDPOINT/ACCESS_KEY/SECRET_KEY "
    "to run live MinIO Object Lock anchor tests"
)
_SKIP = not (_BUCKET and _ENDPOINT and _ACCESS_KEY and _SECRET_KEY)


def _storage() -> MinioObjectLockAnchorStorage:
    return MinioObjectLockAnchorStorage(
        bucket=_BUCKET,
        endpoint=_ENDPOINT,
        region=os.environ.get("SECURGRAPH_AUDIT_ANCHOR_REGION", "us-east-1"),
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        retention_days=1,
    )


@pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_anchor_write_returns_s3_uri() -> None:
    storage = _storage()
    payload = {
        "tenant_id": f"test-anchor-{uuid.uuid4().hex[:8]}",
        "algorithm": "sha256-merkle-v1",
        "merkle_root": "a" * 64,
        "from_sequence": 1,
        "to_sequence": 1,
        "chain_heads": [{"chain_name": "query_traces", "sequence": 1, "last_hash": "a" * 64}],
        "anchored_at": datetime.now(UTC).isoformat(),
    }
    result = await storage.write_anchor(payload)
    assert isinstance(result, AnchorStorageResult)
    assert result.storage_uri.startswith(f"s3://{_BUCKET}/")
    # Object should be retrievable with the same body.
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=_ENDPOINT,
        region_name=os.environ.get("SECURGRAPH_AUDIT_ANCHOR_REGION", "us-east-1"),
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_KEY,
    )
    key = result.storage_uri.replace(f"s3://{_BUCKET}/", "", 1)
    got = client.get_object(Bucket=_BUCKET, Key=key)
    body = json.loads(got["Body"].read().decode("utf-8"))
    assert body["merkle_root"] == payload["merkle_root"]
    assert body["tenant_id"] == payload["tenant_id"]


@pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)
@pytest.mark.asyncio
async def test_anchor_object_is_locked_against_overwrite() -> None:
    """Object Lock must reject overwrite of a retained object."""
    storage = _storage()
    payload = {
        "tenant_id": f"test-lock-{uuid.uuid4().hex[:8]}",
        "algorithm": "sha256-merkle-v1",
        "merkle_root": "b" * 64,
        "from_sequence": 1,
        "to_sequence": 1,
        "chain_heads": [{"chain_name": "query_traces", "sequence": 1, "last_hash": "b" * 64}],
        "anchored_at": datetime.now(UTC).isoformat(),
    }
    result = await storage.write_anchor(payload)
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=_ENDPOINT,
        region_name=os.environ.get("SECURGRAPH_AUDIT_ANCHOR_REGION", "us-east-1"),
        aws_access_key_id=_ACCESS_KEY,
        aws_secret_access_key=_SECRET_KEY,
    )
    key = result.storage_uri.replace(f"s3://{_BUCKET}/", "", 1)
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError):
        client.put_object(
            Bucket=_BUCKET,
            Key=key,
            Body=b"tampered",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=datetime.now(UTC),
        )


def test_production_anchor_requires_bucket() -> None:
    """Production must fail-closed when no MinIO bucket is configured."""
    with pytest.raises(RuntimeError, match="MinIO Object Lock bucket is required"):
        MinioObjectLockAnchorStorage(bucket="", endpoint="http://localhost:9000")


def test_default_anchor_storage_falls_back_to_local_when_no_bucket() -> None:
    """Without a configured bucket, default storage is the local dev adapter."""
    settings = get_settings()
    if settings.audit_anchor_bucket:
        pytest.skip("MinIO bucket is configured in this environment; local fallback not testable")
    from securgraph.indexing.anchors import LocalAnchorStorage, default_anchor_storage

    assert isinstance(default_anchor_storage(), LocalAnchorStorage)
