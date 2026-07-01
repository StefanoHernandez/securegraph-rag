"""Tenant-scoped operational controls for durable ingestion jobs."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from securgraph.adapters.community_generator import generate_hierarchical_communities
from securgraph.adapters.neo4j import get_neo4j_driver
from securgraph.api.dependencies import get_db_session, get_redis_pool
from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.domain import Classification, DocumentType
from securgraph.indexing.anchors import (
    anchor_current_chain_heads,
    should_anchor_current_chain_heads,
)
from securgraph.indexing.db import (
    AdminAuditEventRow,
    AuditAnchorRow,
    IngestionJobRow,
    IngestionSourceRow,
    OperationJobRow,
    PolicyDecisionRow,
    QueryTraceRow,
    RetrievalEvidenceRow,
)
from securgraph.indexing.tamper import verify_table_chain
from securgraph.security.secrets import SecretEncryptionError, decrypt_bytes

router = APIRouter(tags=["operations"])

STALE_RUNNING_AFTER = timedelta(minutes=10)
REPO_ROOT = Path(__file__).resolve().parents[4]
EVAL_RESULTS_DIR = REPO_ROOT / "eval" / "results"


class IngestionJobSummary(BaseModel):
    job_id: str
    trace_id: str
    tenant_id: str
    document_id: str | None
    requested_by: str | None
    owner_id: str | None
    status: str
    current_step: str
    attempt_count: int
    started_at: datetime
    completed_at: datetime | None
    updated_at: datetime
    error: str | None


class IngestionJobList(BaseModel):
    total: int
    jobs: list[IngestionJobSummary]


class ReconcileResult(BaseModel):
    tenant_id: str
    reconciled_job_ids: list[str]


class OperationJobSummary(BaseModel):
    id: str
    tenant_id: str | None
    kind: str
    status: str
    parameters: dict[str, object]
    progress: int
    result: dict[str, object]
    error: str | None
    requested_by: str
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class OperationJobList(BaseModel):
    total: int
    jobs: list[OperationJobSummary]


class EvalBaselineRequest(BaseModel):
    corpus_path: str = Field(default="corpus/mrd_v1", max_length=256)
    timeout_seconds: int = Field(default=3600, ge=60, le=24 * 60 * 60)


def _ensure_tenant_admin(principal: AuthenticatedPrincipal | None, tenant_id: str) -> None:
    if principal is not None and not principal.can_admin_tenant(tenant_id):
        raise HTTPException(status_code=403, detail="Tenant administrator role required.")


def _summary(row: IngestionJobRow) -> IngestionJobSummary:
    return IngestionJobSummary(
        job_id=row.job_id,
        trace_id=row.trace_id,
        tenant_id=row.tenant_id,
        document_id=row.document_id,
        requested_by=row.requested_by,
        owner_id=row.owner_id,
        status=row.status,
        current_step=row.current_step or row.status,
        attempt_count=row.attempt_count,
        started_at=row.started_at,
        completed_at=row.completed_at,
        updated_at=row.updated_at,
        error=row.error,
    )


def _audit(
    session: AsyncSession,
    principal: AuthenticatedPrincipal | None,
    *,
    tenant_id: str,
    action: str,
    job_id: str,
    metadata: dict[str, object] | None = None,
) -> None:
    session.add(
        AdminAuditEventRow(
            id=f"adm_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            actor_user_id=principal.user_id if principal else "system:auth-disabled",
            action=action,
            target_type="ingestion_job",
            target_id=job_id,
            outcome="success",
            event_metadata=metadata or {},
        )
    )


def _operation_summary(row: OperationJobRow) -> OperationJobSummary:
    return OperationJobSummary(
        id=row.id,
        tenant_id=row.tenant_id,
        kind=row.kind,
        status=row.status,
        parameters=row.parameters,
        progress=row.progress,
        result=row.result,
        error=row.error,
        requested_by=row.requested_by,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _create_operation_job(
    session: AsyncSession,
    principal: AuthenticatedPrincipal | None,
    *,
    tenant_id: str,
    kind: str,
    parameters: dict[str, object] | None = None,
) -> OperationJobRow:
    now = datetime.now(UTC)
    job = OperationJobRow(
        id=f"op_{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        kind=kind,
        status="running",
        parameters=parameters or {},
        progress=0,
        result={},
        requested_by=principal.user_id if principal else "system:auth-disabled",
        idempotency_key=f"{tenant_id}:{kind}:{uuid.uuid4().hex}",
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    return job


def _complete_operation_job(row: OperationJobRow, result: dict[str, object]) -> None:
    now = datetime.now(UTC)
    row.status = "completed"
    row.progress = 100
    row.result = result
    row.completed_at = now
    row.updated_at = now


def _fail_operation_job(row: OperationJobRow, error: str) -> None:
    now = datetime.now(UTC)
    row.status = "failed"
    row.error = error
    row.completed_at = now
    row.updated_at = now


def _anchor_result(anchor: AuditAnchorRow) -> dict[str, object]:
    return {
        "anchored": True,
        "from_sequence": anchor.from_sequence,
        "to_sequence": anchor.to_sequence,
        "merkle_root": anchor.merkle_root,
        "storage_uri": anchor.storage_uri,
        "object_version": anchor.object_version,
    }


def _repo_relative_existing_path(path_value: str) -> Path:
    path = (REPO_ROOT / path_value).resolve()
    if REPO_ROOT not in path.parents or not path.exists():
        raise HTTPException(status_code=400, detail="corpus_path must exist inside the repository.")
    return path


def _tail(value: str, *, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def _new_eval_result_files(started_at: datetime) -> list[str]:
    if not EVAL_RESULTS_DIR.exists():
        return []
    started_ts = started_at.timestamp()
    files: list[str] = []
    for path in sorted(EVAL_RESULTS_DIR.glob("baseline-*")):
        if path.is_file() and path.stat().st_mtime >= started_ts:
            files.append(path.relative_to(REPO_ROOT).as_posix())
    return files


async def _run_eval_baseline(
    *,
    corpus_path: str,
    timeout_seconds: int,
) -> dict[str, object]:
    corpus_root = _repo_relative_existing_path(corpus_path)
    eval_python = REPO_ROOT / "eval" / ".venv" / "bin" / "python"
    python_bin = eval_python if eval_python.exists() else Path(sys.executable)
    started_at = datetime.now(UTC)
    env = os.environ.copy()
    env["SECURGRAPH_CORPUS_PATH"] = str(corpus_root)
    process = await asyncio.create_subprocess_exec(
        str(python_bin),
        str(REPO_ROOT / "eval" / "baseline.py"),
        cwd=str(REPO_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        stdout, stderr = await process.communicate()
        return {
            "exit_code": -1,
            "corpus_path": corpus_path,
            "timeout_seconds": timeout_seconds,
            "stdout_tail": _tail(stdout.decode("utf-8", errors="replace")),
            "stderr_tail": _tail(stderr.decode("utf-8", errors="replace")),
            "result_files": _new_eval_result_files(started_at),
            "timed_out": True,
        }
    return {
        "exit_code": process.returncode,
        "corpus_path": corpus_path,
        "timeout_seconds": timeout_seconds,
        "stdout_tail": _tail(stdout.decode("utf-8", errors="replace")),
        "stderr_tail": _tail(stderr.decode("utf-8", errors="replace")),
        "result_files": _new_eval_result_files(started_at),
        "timed_out": False,
    }


@router.get("/tenants/{tenant_id}/ingest-jobs", response_model=IngestionJobList)
async def list_ingestion_jobs(
    tenant_id: str,
    job_status: str | None = Query(default=None, alias="status", max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> IngestionJobList:
    """List tenant ingestion jobs for the Operations console."""
    _ensure_tenant_admin(principal, tenant_id)
    statement = select(IngestionJobRow).where(IngestionJobRow.tenant_id == tenant_id)
    if job_status is not None:
        statement = statement.where(IngestionJobRow.status == job_status)
    result = await session.execute(
        statement.order_by(IngestionJobRow.updated_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return IngestionJobList(total=len(rows), jobs=[_summary(row) for row in rows])


@router.get("/tenants/{tenant_id}/operation-jobs", response_model=OperationJobList)
async def list_operation_jobs(
    tenant_id: str,
    kind: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> OperationJobList:
    """List tenant-scoped administrative operation jobs."""
    _ensure_tenant_admin(principal, tenant_id)
    statement = select(OperationJobRow).where(OperationJobRow.tenant_id == tenant_id)
    if kind is not None:
        statement = statement.where(OperationJobRow.kind == kind)
    result = await session.execute(
        statement.order_by(OperationJobRow.updated_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return OperationJobList(total=len(rows), jobs=[_operation_summary(row) for row in rows])


@router.post(
    "/tenants/{tenant_id}/operation-jobs/ledger-verify",
    response_model=OperationJobSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_ledger_verify_operation(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> OperationJobSummary:
    """Persist and run a tenant ledger verification operation."""
    _ensure_tenant_admin(principal, tenant_id)
    job = _create_operation_job(session, principal, tenant_id=tenant_id, kind="ledger.verify")
    try:
        trace_ok, trace_tampered = await verify_table_chain(session, tenant_id, QueryTraceRow)
        evidence_ok, evidence_tampered = await verify_table_chain(
            session, tenant_id, RetrievalEvidenceRow
        )
        decision_ok, decision_tampered = await verify_table_chain(
            session, tenant_id, PolicyDecisionRow
        )
    except Exception as exc:
        _fail_operation_job(job, "Ledger verification failed.")
        await session.commit()
        raise HTTPException(status_code=503, detail="Ledger verification failed.") from exc
    result: dict[str, object] = {
        "is_intact": trace_ok and evidence_ok and decision_ok,
        "tables": {
            "query_traces": {
                "is_intact": trace_ok,
                "tampered_count": len(trace_tampered),
                "tampered_ids": trace_tampered,
            },
            "retrieval_evidence": {
                "is_intact": evidence_ok,
                "tampered_count": len(evidence_tampered),
                "tampered_ids": evidence_tampered,
            },
            "policy_decisions": {
                "is_intact": decision_ok,
                "tampered_count": len(decision_tampered),
                "tampered_ids": decision_tampered,
            },
        },
    }
    _complete_operation_job(job, result)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="operation.ledger_verify",
        job_id=job.id,
        metadata={"is_intact": result["is_intact"]},
    )
    await session.commit()
    return _operation_summary(job)


@router.post(
    "/tenants/{tenant_id}/operation-jobs/ledger-anchor",
    response_model=OperationJobSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_ledger_anchor_operation(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> OperationJobSummary:
    """Persist and run a tenant Merkle-root audit anchor operation."""
    _ensure_tenant_admin(principal, tenant_id)
    job = _create_operation_job(session, principal, tenant_id=tenant_id, kind="ledger.anchor")
    try:
        anchor = await anchor_current_chain_heads(session, tenant_id)
    except Exception as exc:
        _fail_operation_job(job, "Audit anchoring failed.")
        await session.commit()
        raise HTTPException(status_code=503, detail="Audit anchoring failed.") from exc
    result = _anchor_result(anchor)
    _complete_operation_job(job, result)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="operation.ledger_anchor",
        job_id=job.id,
        metadata={"to_sequence": anchor.to_sequence, "storage_uri": anchor.storage_uri},
    )
    await session.commit()
    return _operation_summary(job)


@router.post(
    "/tenants/{tenant_id}/operation-jobs/ledger-anchor-due",
    response_model=OperationJobSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_due_ledger_anchor_operation(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> OperationJobSummary:
    """Anchor tenant chain heads only when the configured schedule is due."""
    _ensure_tenant_admin(principal, tenant_id)
    job = _create_operation_job(session, principal, tenant_id=tenant_id, kind="ledger.anchor_due")
    due = await should_anchor_current_chain_heads(session, tenant_id)
    if not due:
        result: dict[str, object] = {"anchored": False, "reason": "not_due"}
        _complete_operation_job(job, result)
        _audit(
            session,
            principal,
            tenant_id=tenant_id,
            action="operation.ledger_anchor_due",
            job_id=job.id,
            metadata=result,
        )
        await session.commit()
        return _operation_summary(job)
    try:
        anchor = await anchor_current_chain_heads(session, tenant_id)
    except Exception as exc:
        _fail_operation_job(job, "Audit anchoring failed.")
        await session.commit()
        raise HTTPException(status_code=503, detail="Audit anchoring failed.") from exc
    result = _anchor_result(anchor)
    _complete_operation_job(job, result)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="operation.ledger_anchor_due",
        job_id=job.id,
        metadata={"to_sequence": anchor.to_sequence, "storage_uri": anchor.storage_uri},
    )
    await session.commit()
    return _operation_summary(job)


@router.post(
    "/tenants/{tenant_id}/operation-jobs/community-recompute",
    response_model=OperationJobSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_community_recompute_operation(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> OperationJobSummary:
    """Persist and run a tenant community recomputation operation."""
    _ensure_tenant_admin(principal, tenant_id)
    job = _create_operation_job(
        session,
        principal,
        tenant_id=tenant_id,
        kind="community.recompute",
    )
    try:
        result = await generate_hierarchical_communities(
            driver=get_neo4j_driver(),
            tenant_id=tenant_id,
        )
    except Exception as exc:
        _fail_operation_job(job, "Community recompute failed.")
        await session.commit()
        raise HTTPException(status_code=503, detail="Community recompute failed.") from exc
    payload: dict[str, object] = dict(result)
    _complete_operation_job(job, payload)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="operation.community_recompute",
        job_id=job.id,
        metadata=payload,
    )
    await session.commit()
    return _operation_summary(job)


@router.post(
    "/tenants/{tenant_id}/operation-jobs/eval-baseline",
    response_model=OperationJobSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_eval_baseline_operation(
    tenant_id: str,
    request: EvalBaselineRequest,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> OperationJobSummary:
    """Persist and run the isolated baseline/RAGAS evaluation harness."""
    _ensure_tenant_admin(principal, tenant_id)
    parameters: dict[str, object] = {
        "corpus_path": request.corpus_path,
        "timeout_seconds": request.timeout_seconds,
    }
    job = _create_operation_job(
        session,
        principal,
        tenant_id=tenant_id,
        kind="eval.baseline",
        parameters=parameters,
    )
    try:
        result = await _run_eval_baseline(
            corpus_path=request.corpus_path,
            timeout_seconds=request.timeout_seconds,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _fail_operation_job(job, "Baseline evaluation failed.")
        await session.commit()
        raise HTTPException(status_code=503, detail="Baseline evaluation failed.") from exc
    if result.get("exit_code") != 0:
        _fail_operation_job(job, "Baseline evaluation exited with a non-zero status.")
        job.result = result
    else:
        _complete_operation_job(job, result)
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="operation.eval_baseline",
        job_id=job.id,
        metadata={"exit_code": result.get("exit_code")},
    )
    await session.commit()
    return _operation_summary(job)


@router.post(
    "/tenants/{tenant_id}/ingest-jobs/{job_id}/retry",
    response_model=IngestionJobSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_ingestion_job(
    tenant_id: str,
    job_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
    redis: ArqRedis = Depends(get_redis_pool),
) -> IngestionJobSummary:
    """Requeue a failed job using its encrypted persisted source."""
    _ensure_tenant_admin(principal, tenant_id)
    job = await session.get(IngestionJobRow, job_id)
    if job is None or job.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Ingestion job not found.")
    if job.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed ingestion jobs can be retried.")

    source_result = await session.execute(
        select(IngestionSourceRow).where(IngestionSourceRow.job_id == job_id)
    )
    source = source_result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=409, detail="No durable source is available for this job.")
    try:
        data = decrypt_bytes(
            source.encrypted_data,
            associated_data=f"tenant:{tenant_id}:ingest:{job_id}",
        )
    except SecretEncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Encrypted source cannot be opened with the configured master key.",
        ) from exc

    job.status = "queued"
    job.current_step = "queued"
    job.completed_at = None
    job.error = None
    _audit(
        session,
        principal,
        tenant_id=tenant_id,
        action="ingestion.retry",
        job_id=job_id,
        metadata={"previous_attempt_count": job.attempt_count},
    )
    await session.commit()

    request_dict = {
        "tenant_id": tenant_id,
        "source_uri": source.source_uri,
        "owner_id": f"user:{job.owner_id}" if job.owner_id else "user:anonymous",
        "data": data,
        "document_type": DocumentType(source.document_type).value,
        "classification": Classification(source.classification).value,
        "policy_tags": list(source.policy_tags),
        "direct_viewers": list(source.direct_viewers),
        "title_override": source.title_override,
        "source_format": source.source_format,
    }
    try:
        await redis.enqueue_job("async_ingest_task", request_dict, job.job_id, job.trace_id)
    except Exception as exc:
        job.status = "failed"
        job.current_step = "enqueue_failed"
        job.error = "Retry could not be queued."
        await session.commit()
        raise HTTPException(
            status_code=503,
            detail="Ingestion worker queue is unavailable.",
        ) from exc
    return _summary(job)


@router.post("/tenants/{tenant_id}/ingest-jobs/reconcile", response_model=ReconcileResult)
async def reconcile_stale_ingestion_jobs(
    tenant_id: str,
    principal: AuthenticatedPrincipal | None = Depends(require_user),
    session: AsyncSession = Depends(get_db_session),
) -> ReconcileResult:
    """Mark jobs stuck in running state as failed so an administrator can retry them."""
    _ensure_tenant_admin(principal, tenant_id)
    cutoff = datetime.now(UTC) - STALE_RUNNING_AFTER
    result = await session.execute(
        select(IngestionJobRow).where(
            IngestionJobRow.tenant_id == tenant_id,
            IngestionJobRow.status == "running",
            IngestionJobRow.updated_at < cutoff,
        )
    )
    rows = result.scalars().all()
    for row in rows:
        row.status = "failed"
        row.current_step = "reconciled"
        row.completed_at = datetime.now(UTC)
        row.error = "Worker heartbeat expired; job marked failed by reconciliation."
        _audit(
            session,
            principal,
            tenant_id=tenant_id,
            action="ingestion.reconcile",
            job_id=row.job_id,
            metadata={"reason": "worker_heartbeat_expired"},
        )
    return ReconcileResult(tenant_id=tenant_id, reconciled_job_ids=[row.job_id for row in rows])
