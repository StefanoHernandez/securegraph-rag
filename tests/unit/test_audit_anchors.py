"""Audit anchor service tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from securgraph.indexing.anchors import (
    AnchorStorageResult,
    LocalAnchorStorage,
    anchor_current_chain_heads,
    compute_merkle_root,
    is_anchor_due,
)
from securgraph.indexing.db import AuditAnchorRow, AuditChainHeadRow

pytestmark = pytest.mark.unit


class FakeStorage:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def write_anchor(self, payload: dict[str, object]) -> AnchorStorageResult:
        self.payloads.append(payload)
        return AnchorStorageResult(
            storage_uri="s3://securegraph-audit/anchors/mrd/root.json",
            object_version="v1",
        )


def test_compute_merkle_root_is_order_independent() -> None:
    leaves_a = ["trace:2:bbb", "decision:1:aaa", "evidence:4:ccc"]
    leaves_b = list(reversed(leaves_a))

    assert compute_merkle_root(leaves_a) == compute_merkle_root(leaves_b)


async def test_anchor_current_chain_heads_persists_row() -> None:
    session = SimpleNamespace()
    session.added = []
    session.add = session.added.append
    heads = [
        AuditChainHeadRow(
            tenant_id="mrd",
            chain_name="query_traces",
            last_hash="a" * 64,
            sequence=7,
            updated_at=datetime.now(UTC),
        ),
        AuditChainHeadRow(
            tenant_id="mrd",
            chain_name="policy_decisions",
            last_hash="b" * 64,
            sequence=3,
            updated_at=datetime.now(UTC),
        ),
    ]

    class Result:
        def scalars(self):
            return self

        def all(self):
            return heads

    async def execute(_statement):
        return Result()

    session.execute = execute
    storage = FakeStorage()

    anchor = await anchor_current_chain_heads(session, "mrd", storage=storage)

    assert isinstance(anchor, AuditAnchorRow)
    assert anchor.tenant_id == "mrd"
    assert anchor.from_sequence == 1
    assert anchor.to_sequence == 10
    assert anchor.object_version == "v1"
    assert anchor.storage_uri == "s3://securegraph-audit/anchors/mrd/root.json"
    assert session.added == [anchor]
    assert storage.payloads[0]["tenant_id"] == "mrd"
    assert storage.payloads[0]["algorithm"] == "sha256-merkle-v1"


async def test_local_anchor_storage_rejected_in_production() -> None:
    storage = LocalAnchorStorage(environment="prod")

    with pytest.raises(RuntimeError, match="S3 Object Lock"):
        await storage.write_anchor({"tenant_id": "mrd"})


def test_anchor_due_without_previous_anchor() -> None:
    assert is_anchor_due(total_sequence=1, last_anchor=None)
    assert not is_anchor_due(total_sequence=0, last_anchor=None)


def test_anchor_due_after_interval_only_when_new_events_exist() -> None:
    now = datetime.now(UTC)
    anchor = AuditAnchorRow(
        id="anc_old",
        tenant_id="mrd",
        from_sequence=1,
        to_sequence=10,
        merkle_root="a" * 64,
        storage_uri="s3://bucket/root.json",
        anchored_at=now - timedelta(seconds=3601),
    )

    assert not is_anchor_due(total_sequence=10, last_anchor=anchor, now=now)
    assert is_anchor_due(total_sequence=11, last_anchor=anchor, now=now)


def test_anchor_due_after_event_threshold() -> None:
    now = datetime.now(UTC)
    anchor = AuditAnchorRow(
        id="anc_recent",
        tenant_id="mrd",
        from_sequence=1,
        to_sequence=10,
        merkle_root="a" * 64,
        storage_uri="s3://bucket/root.json",
        anchored_at=now,
    )

    assert is_anchor_due(
        total_sequence=10_010,
        last_anchor=anchor,
        now=now,
        interval_seconds=3600,
        event_threshold=10_000,
    )
