"""Regression test: the async ingestion worker must never persist raw exception text.

`async_ingest_task` is the only ingestion path in stage and prod
(`enforce_worker_hygiene`). Before this fix it stored `str(e)` directly in the
`error` column, which is returned verbatim by
`/ingest/status/{job_id}` and `/tenants/{tenant_id}/ingest-jobs` and rendered
raw in the ingest and operations UI screens. Since `ingest_document` writes to
Postgres, Qdrant and Neo4j, `str(e)` there can carry a SQLAlchemy statement
(and its bound parameters) or a connection error's host/port/user.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from securgraph.ingestion.worker import async_ingest_task

pytestmark = pytest.mark.unit

SENTINEL = "SELECT secret_column FROM classified_chunks"


async def test_worker_never_persists_raw_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw SQL-bearing exception must not reach the stored `error` value."""
    status_updates: list[dict[str, object]] = []

    class FakeSession:
        def __init__(self) -> None:
            self.commit = AsyncMock()
            self.rollback = AsyncMock()

    class FakeSessionContext:
        def __init__(self, session: FakeSession) -> None:
            self.session = session

        async def __aenter__(self) -> FakeSession:
            return self.session

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakeSessionMaker:
        def __call__(self) -> FakeSessionContext:
            return FakeSessionContext(FakeSession())

    async def noop_set_tenant_context(*args: object, **kwargs: object) -> None:
        return None

    async def record_job_update(*args: object, **kwargs: object) -> None:
        status_updates.append(dict(kwargs))

    async def fail_ingest(*args: object, **kwargs: object) -> None:
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(
        "securgraph.ingestion.worker.set_tenant_rls_context",
        noop_set_tenant_context,
    )
    monkeypatch.setattr("securgraph.ingestion.worker.update_ingestion_job", record_job_update)
    monkeypatch.setattr("securgraph.ingestion.worker.ingest_document", fail_ingest)

    request_dict = {
        "tenant_id": "mrd",
        "source_uri": "file://policy.md",
        "owner_id": "user:alice",
        "data": b"# policy",
        "document_type": "policy",
        "classification": "internal",
        "policy_tags": [],
        "direct_viewers": [],
        "title_override": None,
        "source_format": None,
    }

    with pytest.raises(RuntimeError, match=SENTINEL):
        await async_ingest_task(
            {"session_maker": FakeSessionMaker()},
            request_dict,
            "ing-1",
            "trace-1",
        )

    failure_updates = [update for update in status_updates if update.get("status") == "failed"]
    assert len(failure_updates) == 1
    stored_error = failure_updates[0]["error"]
    assert isinstance(stored_error, str)
    assert SENTINEL not in stored_error
