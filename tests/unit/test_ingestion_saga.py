"""Unit tests for cross-store ingestion compensation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.indexing.postgres_writer import record_ingestion_saga_step
from securgraph.ingestion.saga import IngestionSaga
from securgraph.ingestion.service import IngestionCompensationError

pytestmark = pytest.mark.unit


async def test_saga_compensates_completed_external_steps_in_reverse_order() -> None:
    journal = AsyncMock()
    qdrant_cleanup = AsyncMock()
    openfga_cleanup = AsyncMock()
    neo4j_cleanup = AsyncMock()
    saga = IngestionSaga(
        job_id="ing-1",
        tenant_id="mrd",
        journal=journal,
        compensators={
            "qdrant": qdrant_cleanup,
            "openfga": openfga_cleanup,
            "neo4j": neo4j_cleanup,
        },
    )

    await saga.record_completed("qdrant", {"chunk_ids": ["chunk-1"]})
    await saga.record_completed("openfga", {"document_id": "doc-1"})
    await saga.record_completed("neo4j", {"document_id": "doc-1"})

    outcome = await saga.compensate()

    assert outcome.reconcile_required is False
    assert outcome.errors == ()
    neo4j_cleanup.assert_awaited_once_with({"document_id": "doc-1"})
    openfga_cleanup.assert_awaited_once_with({"document_id": "doc-1"})
    qdrant_cleanup.assert_awaited_once_with({"chunk_ids": ["chunk-1"]})
    assert [call.args[1] for call in journal.await_args_list] == [
        "completed",
        "completed",
        "completed",
        "compensated",
        "compensated",
        "compensated",
    ]


async def test_saga_requires_reconciliation_when_a_compensator_fails() -> None:
    journal = AsyncMock()
    failing_cleanup = AsyncMock(side_effect=RuntimeError("neo4j unavailable"))
    qdrant_cleanup = AsyncMock()
    saga = IngestionSaga(
        job_id="ing-2",
        tenant_id="mrd",
        journal=journal,
        compensators={"qdrant": qdrant_cleanup, "neo4j": failing_cleanup},
    )
    await saga.record_completed("qdrant", {"chunk_ids": ["chunk-1"]})
    await saga.record_completed("neo4j", {"document_id": "doc-1"})

    outcome = await saga.compensate()

    assert outcome.reconcile_required is True
    assert outcome.errors == ("neo4j: neo4j unavailable",)
    qdrant_cleanup.assert_awaited_once_with({"chunk_ids": ["chunk-1"]})
    assert journal.await_args_list[-2].args[1] == "reconcile_required"
    assert journal.await_args_list[-1].args[1] == "compensated"


async def test_saga_compensates_a_started_step_after_an_ambiguous_store_failure() -> None:
    journal = AsyncMock()
    qdrant_cleanup = AsyncMock()
    saga = IngestionSaga(
        job_id="ing-ambiguous",
        tenant_id="mrd",
        journal=journal,
        compensators={"qdrant": qdrant_cleanup},
    )

    await saga.record_started("qdrant", {"chunk_ids": ["chunk-1"]})

    outcome = await saga.compensate()

    assert outcome.reconcile_required is False
    qdrant_cleanup.assert_awaited_once_with({"chunk_ids": ["chunk-1"]})
    assert [call.args[1] for call in journal.await_args_list] == ["started", "compensated"]


async def test_saga_step_ledger_is_idempotent_by_job_and_step() -> None:
    session = MagicMock()
    session.get = AsyncMock(return_value=None)

    await record_ingestion_saga_step(
        session,
        job_id="ing-1",
        tenant_id="mrd",
        step="qdrant",
        status="completed",
        resource_ids={"chunk_ids": ["chunk-1"]},
    )

    row = session.add.call_args.args[0]
    assert row.job_id == "ing-1"
    assert row.tenant_id == "mrd"
    assert row.step == "qdrant"
    assert row.resource_ids == {"chunk_ids": ["chunk-1"]}
    assert row.compensation_status is None


async def test_worker_saga_journal_commits_each_transition_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from securgraph.ingestion.worker import build_saga_journal

    session = MagicMock()
    session.commit = AsyncMock()

    class SessionContext:
        async def __aenter__(self) -> MagicMock:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    set_context = AsyncMock()
    record_step = AsyncMock()
    monkeypatch.setattr("securgraph.ingestion.worker.set_tenant_rls_context", set_context)
    monkeypatch.setattr("securgraph.ingestion.worker.record_ingestion_saga_step", record_step)

    journal = build_saga_journal(lambda: SessionContext(), job_id="ing-1", tenant_id="mrd")
    await journal("qdrant", "completed", {"chunk_ids": ["chunk-1"]}, None, None)

    set_context.assert_awaited_once_with(session, tenant_id="mrd", is_super_admin=False)
    record_step.assert_awaited_once_with(
        session,
        job_id="ing-1",
        tenant_id="mrd",
        step="qdrant",
        status="completed",
        resource_ids={"chunk_ids": ["chunk-1"]},
        compensation_status=None,
        error=None,
    )
    session.commit.assert_awaited_once()


def test_worker_marks_uncompensated_ingestion_for_manual_reconciliation() -> None:
    from securgraph.ingestion.worker import ingestion_failure_status

    reconciliation_error = IngestionCompensationError(
        RuntimeError("write failed"),
        ("qdrant: unavailable",),
    )

    assert ingestion_failure_status(RuntimeError("write failed")) == "failed"
    assert ingestion_failure_status(reconciliation_error) == "reconcile_required"
