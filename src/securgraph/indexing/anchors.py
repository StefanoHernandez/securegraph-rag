"""Merkle anchoring for tamper-evident audit chain heads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.config import get_settings
from securgraph.indexing.db import AuditAnchorRow, AuditChainHeadRow

ANCHOR_ALGORITHM = "sha256-merkle-v1"
DEFAULT_ANCHOR_INTERVAL_SECONDS = 3600
DEFAULT_ANCHOR_EVENT_THRESHOLD = 10_000


@dataclass(frozen=True)
class AnchorStorageResult:
    storage_uri: str
    object_version: str | None = None


class AnchorStorage(Protocol):
    async def write_anchor(self, payload: dict[str, object]) -> AnchorStorageResult:
        """Persist an immutable anchor payload and return its durable URI."""


class LocalAnchorStorage:
    """Development/test anchor storage.

    Production must use S3 Object Lock. This class deliberately refuses to run
    when environment is production-like.
    """

    def __init__(self, *, environment: str, root: Path | None = None) -> None:
        self.environment = environment
        self.root = root or Path(".securgraph-audit-anchors")

    async def write_anchor(self, payload: dict[str, object]) -> AnchorStorageResult:
        if self.environment.lower() in {"prod", "production"}:
            raise RuntimeError("S3 Object Lock anchor storage is required in production.")
        tenant_id = str(payload["tenant_id"])
        merkle_root = str(payload["merkle_root"])
        directory = self.root / tenant_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{merkle_root}.json"
        path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return AnchorStorageResult(storage_uri=path.as_posix(), object_version=None)


class S3ObjectLockAnchorStorage:
    """S3 Object Lock anchor storage.

    Uses boto3 only when this storage is instantiated, keeping the default API
    process free from an AWS dependency in development/test environments.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "securegraph/audit-anchors",
        retention_days: int = 365,
    ) -> None:
        if not bucket:
            raise RuntimeError("S3 Object Lock bucket is required for audit anchoring.")
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.retention_days = retention_days

    async def write_anchor(self, payload: dict[str, object]) -> AnchorStorageResult:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for S3 Object Lock audit anchoring.") from exc

        tenant_id = str(payload["tenant_id"])
        merkle_root = str(payload["merkle_root"])
        key = f"{self.prefix}/{tenant_id}/{merkle_root}.json"
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        client = boto3.client("s3")
        retain_until = datetime.now(UTC) + timedelta(days=self.retention_days)
        response = await asyncio.to_thread(
            client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
        )
        return AnchorStorageResult(
            storage_uri=f"s3://{self.bucket}/{key}",
            object_version=response.get("VersionId"),
        )


def compute_merkle_root(leaves: list[str]) -> str:
    """Compute an order-independent SHA-256 Merkle root for anchor leaves."""
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level = sorted(hashlib.sha256(leaf.encode("utf-8")).hexdigest() for leaf in leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            hashlib.sha256((level[index] + level[index + 1]).encode("ascii")).hexdigest()
            for index in range(0, len(level), 2)
        ]
    return level[0]


def default_anchor_storage() -> AnchorStorage:
    settings = get_settings()
    if settings.audit_anchor_s3_bucket:
        return S3ObjectLockAnchorStorage(
            bucket=settings.audit_anchor_s3_bucket,
            prefix=settings.audit_anchor_s3_prefix,
            retention_days=settings.audit_anchor_retention_days,
        )
    return LocalAnchorStorage(environment=settings.env)


def is_anchor_due(
    *,
    total_sequence: int,
    last_anchor: AuditAnchorRow | None,
    now: datetime | None = None,
    interval_seconds: int = DEFAULT_ANCHOR_INTERVAL_SECONDS,
    event_threshold: int = DEFAULT_ANCHOR_EVENT_THRESHOLD,
) -> bool:
    """Return whether chain heads need a new external anchor.

    A new anchor is due when no previous anchor exists for non-empty heads, when
    10k new audit events have appeared, or when at least one new audit event
    exists and the last anchor is older than the configured interval.
    """
    if total_sequence <= 0:
        return False
    if last_anchor is None:
        return True
    new_events = total_sequence - last_anchor.to_sequence
    if new_events <= 0:
        return False
    if new_events >= event_threshold:
        return True
    reference_now = now or datetime.now(UTC)
    anchored_at = last_anchor.anchored_at
    if anchored_at.tzinfo is None:
        anchored_at = anchored_at.replace(tzinfo=UTC)
    return (reference_now - anchored_at).total_seconds() >= interval_seconds


async def should_anchor_current_chain_heads(
    session: AsyncSession,
    tenant_id: str,
    *,
    now: datetime | None = None,
    interval_seconds: int | None = None,
    event_threshold: int | None = None,
) -> bool:
    """Check whether a tenant's chain heads are due for periodic anchoring."""
    settings = get_settings()
    heads_result = await session.execute(
        select(AuditChainHeadRow).where(AuditChainHeadRow.tenant_id == tenant_id)
    )
    total_sequence = sum(head.sequence for head in heads_result.scalars().all())
    anchor_result = await session.execute(
        select(AuditAnchorRow)
        .where(AuditAnchorRow.tenant_id == tenant_id)
        .order_by(AuditAnchorRow.anchored_at.desc())
        .limit(1)
    )
    last_anchor = anchor_result.scalars().first()
    return is_anchor_due(
        total_sequence=total_sequence,
        last_anchor=last_anchor,
        now=now,
        interval_seconds=interval_seconds or settings.audit_anchor_interval_seconds,
        event_threshold=event_threshold or settings.audit_anchor_event_threshold,
    )


async def anchor_current_chain_heads(
    session: AsyncSession,
    tenant_id: str,
    *,
    storage: AnchorStorage | None = None,
) -> AuditAnchorRow:
    """Anchor the current tenant audit chain heads as one Merkle root."""
    result = await session.execute(
        select(AuditChainHeadRow)
        .where(AuditChainHeadRow.tenant_id == tenant_id)
        .order_by(AuditChainHeadRow.chain_name)
    )
    heads = result.scalars().all()
    leaves = [
        f"{head.chain_name}:{head.sequence}:{head.last_hash}"
        for head in heads
        if head.sequence > 0
    ]
    merkle_root = compute_merkle_root(leaves)
    to_sequence = sum(head.sequence for head in heads)
    payload: dict[str, object] = {
        "tenant_id": tenant_id,
        "algorithm": ANCHOR_ALGORITHM,
        "merkle_root": merkle_root,
        "from_sequence": 1 if to_sequence else 0,
        "to_sequence": to_sequence,
        "chain_heads": [
            {
                "chain_name": head.chain_name,
                "sequence": head.sequence,
                "last_hash": head.last_hash,
            }
            for head in heads
        ],
        "anchored_at": datetime.now(UTC).isoformat(),
    }
    storage_result = await (storage or default_anchor_storage()).write_anchor(payload)
    from_sequence = 1 if to_sequence else 0
    row = AuditAnchorRow(
        id=f"anc_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        from_sequence=from_sequence,
        to_sequence=to_sequence,
        merkle_root=merkle_root,
        storage_uri=storage_result.storage_uri,
        object_version=storage_result.object_version,
        anchored_at=datetime.now(UTC),
    )
    session.add(row)
    return row
