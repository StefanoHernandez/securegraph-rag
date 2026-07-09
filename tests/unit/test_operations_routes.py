"""Tenant-scoped operations route tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from securgraph.api.dependencies import get_db_session
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.indexing.db import OperationJobRow

pytestmark = pytest.mark.unit


def principal(role: str = "tenant_admin", tenant_id: str = "mrd") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        user_id="admin",
        username="admin",
        tenant_id=tenant_id,
        role=role,
        is_super_admin=role == "super_admin",
        must_change_password=False,
        session_id="ses-admin",
    )


@pytest.fixture
def operations_session(fastapi_app: FastAPI) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()

    async def override_db() -> AsyncIterator[AsyncMock]:
        yield session

    fastapi_app.dependency_overrides[get_db_session] = override_db
    return session


async def test_tenant_admin_lists_operation_jobs(
    fastapi_app: FastAPI, client: AsyncClient, operations_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()
    job = OperationJobRow(
        id="op-1",
        tenant_id="mrd",
        kind="ledger.verify",
        status="completed",
        parameters={},
        progress=100,
        result={"is_intact": True},
        requested_by="admin",
        idempotency_key="mrd:ledger.verify:1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [job]
    operations_session.execute.return_value = result

    response = await client.get("/tenants/mrd/operation-jobs")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["jobs"][0]["id"] == "op-1"
    assert response.json()["jobs"][0]["kind"] == "ledger.verify"


async def test_viewer_cannot_start_operation_job(
    fastapi_app: FastAPI, client: AsyncClient
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal(role="viewer")

    response = await client.post("/tenants/mrd/operation-jobs/ledger-verify", json={})

    assert response.status_code == 403


async def test_tenant_admin_runs_ledger_verify_operation(
    fastapi_app: FastAPI,
    client: AsyncClient,
    operations_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()

    async def fake_verify_table_chain(_session, _tenant_id, row_type):
        if row_type.__name__ == "PolicyDecisionRow":
            return False, ["pol-1"]
        return True, []

    monkeypatch.setattr(
        "securgraph.api.routes.operations.verify_table_chain",
        fake_verify_table_chain,
    )

    response = await client.post("/tenants/mrd/operation-jobs/ledger-verify", json={})

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "ledger.verify"
    assert body["status"] == "completed"
    assert body["progress"] == 100
    assert body["result"]["is_intact"] is False
    added = [call.args[0] for call in operations_session.add.call_args_list]
    operation_jobs = [row for row in added if isinstance(row, OperationJobRow)]
    assert operation_jobs[0].tenant_id == "mrd"
    assert operation_jobs[0].requested_by == "admin"


async def test_tenant_admin_runs_community_recompute_operation(
    fastapi_app: FastAPI,
    client: AsyncClient,
    operations_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()
    generator = AsyncMock(return_value={"communities": 3})
    monkeypatch.setattr(
        "securgraph.api.routes.operations.generate_hierarchical_communities",
        generator,
    )
    monkeypatch.setattr(
        "securgraph.api.routes.operations.get_neo4j_driver",
        lambda: "driver",
    )

    response = await client.post("/tenants/mrd/operation-jobs/community-recompute", json={})

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "community.recompute"
    assert body["status"] == "completed"
    assert body["result"] == {"communities": 3}
    generator.assert_awaited_once()


async def test_tenant_admin_runs_eval_baseline_operation(
    fastapi_app: FastAPI,
    client: AsyncClient,
    operations_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()

    async def fake_eval_runner(*, corpus_path: str, timeout_seconds: int):
        return {
            "exit_code": 0,
            "corpus_path": corpus_path,
            "timeout_seconds": timeout_seconds,
            "stdout_tail": "Wrote: eval/results/baseline.csv",
            "stderr_tail": "",
            "result_files": ["eval/results/baseline.csv"],
        }

    monkeypatch.setattr("securgraph.api.routes.operations._run_eval_baseline", fake_eval_runner)

    response = await client.post(
        "/tenants/mrd/operation-jobs/eval-baseline",
        json={"corpus_path": "corpus/mrd_v1", "timeout_seconds": 900},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "eval.baseline"
    assert body["status"] == "completed"
    assert body["parameters"] == {
        "corpus_path": "corpus/mrd_v1",
        "timeout_seconds": 900,
    }
    assert body["result"]["result_files"] == ["eval/results/baseline.csv"]
    added = [call.args[0] for call in operations_session.add.call_args_list]
    operation_jobs = [row for row in added if isinstance(row, OperationJobRow)]
    assert operation_jobs[0].kind == "eval.baseline"


async def test_tenant_admin_runs_ledger_anchor_operation(
    fastapi_app: FastAPI,
    client: AsyncClient,
    operations_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()

    async def fake_anchor_current_chain_heads(_session, tenant_id: str):
        return SimpleNamespace(
            tenant_id=tenant_id,
            from_sequence=1,
            to_sequence=42,
            merkle_root="a" * 64,
            storage_uri="s3://securegraph-audit/anchors/mrd/root.json",
            object_version="v1",
        )

    monkeypatch.setattr(
        "securgraph.api.routes.operations.anchor_current_chain_heads",
        fake_anchor_current_chain_heads,
    )

    response = await client.post("/tenants/mrd/operation-jobs/ledger-anchor", json={})

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "ledger.anchor"
    assert body["status"] == "completed"
    assert body["result"]["to_sequence"] == 42
    assert body["result"]["storage_uri"] == "s3://securegraph-audit/anchors/mrd/root.json"


async def test_tenant_admin_runs_due_ledger_anchor_when_due(
    fastapi_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()

    async def fake_should_anchor(_session, tenant_id: str):
        assert tenant_id == "mrd"
        return True

    async def fake_anchor_current_chain_heads(_session, tenant_id: str):
        return SimpleNamespace(
            tenant_id=tenant_id,
            from_sequence=1,
            to_sequence=10_001,
            merkle_root="b" * 64,
            storage_uri="s3://securegraph-audit/anchors/mrd/root.json",
            object_version="v2",
        )

    monkeypatch.setattr(
        "securgraph.api.routes.operations.should_anchor_current_chain_heads",
        fake_should_anchor,
    )
    monkeypatch.setattr(
        "securgraph.api.routes.operations.anchor_current_chain_heads",
        fake_anchor_current_chain_heads,
    )

    response = await client.post("/tenants/mrd/operation-jobs/ledger-anchor-due", json={})

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "ledger.anchor_due"
    assert body["status"] == "completed"
    assert body["result"]["anchored"] is True
    assert body["result"]["to_sequence"] == 10_001


async def test_tenant_admin_skips_due_ledger_anchor_when_not_due(
    fastapi_app: FastAPI,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()

    async def fake_should_anchor(_session, tenant_id: str):
        assert tenant_id == "mrd"
        return False

    monkeypatch.setattr(
        "securgraph.api.routes.operations.should_anchor_current_chain_heads",
        fake_should_anchor,
    )

    response = await client.post("/tenants/mrd/operation-jobs/ledger-anchor-due", json={})

    assert response.status_code == 202
    body = response.json()
    assert body["kind"] == "ledger.anchor_due"
    assert body["status"] == "completed"
    assert body["result"] == {"anchored": False, "reason": "not_due"}


async def test_tenant_admin_cancels_running_operation_job(
    fastapi_app: FastAPI, client: AsyncClient, operations_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()
    job = OperationJobRow(
        id="op-cancel-1",
        tenant_id="mrd",
        kind="community.recompute",
        status="running",
        parameters={},
        progress=30,
        result={},
        requested_by="admin",
        idempotency_key="mrd:community.recompute:cancel-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    operations_session.get = AsyncMock(return_value=job)

    response = await client.post("/tenants/mrd/operation-jobs/op-cancel-1/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert job.status == "cancelled"
    assert job.completed_at is not None


async def test_cancel_rejects_already_terminal_job(
    fastapi_app: FastAPI, client: AsyncClient, operations_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()
    job = OperationJobRow(
        id="op-done-1",
        tenant_id="mrd",
        kind="ledger.verify",
        status="completed",
        parameters={},
        progress=100,
        result={"is_intact": True},
        requested_by="admin",
        idempotency_key="mrd:ledger.verify:done-1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    operations_session.get = AsyncMock(return_value=job)

    response = await client.post("/tenants/mrd/operation-jobs/op-done-1/cancel")

    assert response.status_code == 409


async def test_cancel_rejects_wrong_tenant(
    fastapi_app: FastAPI, client: AsyncClient, operations_session: AsyncMock
) -> None:
    fastapi_app.dependency_overrides[require_user] = lambda: principal()
    job = OperationJobRow(
        id="op-other-1",
        tenant_id="other-tenant",
        kind="ledger.verify",
        status="running",
        parameters={},
        progress=10,
        result={},
        requested_by="admin",
        idempotency_key="other:ledger.verify:1",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    operations_session.get = AsyncMock(return_value=job)

    response = await client.post("/tenants/mrd/operation-jobs/op-other-1/cancel")

    assert response.status_code == 403


async def test_cancel_audit_records_true_previous_status(
    fastapi_app: FastAPI, client: AsyncClient, operations_session: AsyncMock
) -> None:
    """The cancel audit event must record the status BEFORE cancellation."""
    from securgraph.indexing.db import AdminAuditEventRow

    fastapi_app.dependency_overrides[require_user] = lambda: principal()
    job = OperationJobRow(
        id="op-cancel-2",
        tenant_id="mrd",
        kind="community.recompute",
        status="running",
        parameters={},
        progress=10,
        result={},
        requested_by="admin",
        idempotency_key="mrd:community.recompute:cancel-2",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    operations_session.get = AsyncMock(return_value=job)
    added: list = []
    operations_session.add = MagicMock(side_effect=added.append)

    response = await client.post("/tenants/mrd/operation-jobs/op-cancel-2/cancel")
    assert response.status_code == 200

    events = [row for row in added if isinstance(row, AdminAuditEventRow)]
    assert len(events) == 1
    assert events[0].event_metadata["previous_status"] == "running"
