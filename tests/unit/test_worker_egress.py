"""The ingestion worker must load the tenant's egress policy before it calls out.

`async_ingest_task` runs in the arq process, which never handles a settings
PUT. `set_runtime_override` writes to a per-process dict, so without an
explicit reload the worker's override map is empty — and
`enforce_egress` reads an empty map as "no policy stored", which is its allow
branch. A tenant that blocked a provider would have that policy honoured on
the query path and silently ignored on the async ingestion path, which is the
only ingestion path in stage and prod.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from securgraph.adapters.llm import get_llm_adapter_for, reset_llm_adapter_cache
from securgraph.ingestion.worker import async_ingest_task
from securgraph.policy.egress import EgressBlockedError
from securgraph.runtime_overrides import clear_runtime_overrides, set_runtime_override

pytestmark = pytest.mark.unit

BLOCK_EVERYTHING = {
    "default": "block",
    "rules": [],
}

REQUEST_DICT = {
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


class _FakeSession:
    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


class _FakeSessionContext:
    def __init__(self) -> None:
        self.session = _FakeSession()

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSessionMaker:
    def __call__(self) -> _FakeSessionContext:
        return _FakeSessionContext()


@pytest.fixture(autouse=True)
def _clean_state():
    clear_runtime_overrides()
    reset_llm_adapter_cache()
    yield
    clear_runtime_overrides()
    reset_llm_adapter_cache()


async def test_worker_loads_the_egress_policy_before_building_an_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed: a blocking policy stored for the tenant stops the job.

    The fake hydrate stands in for the database read — it populates the
    process map the way the real one does. `ingest_document` is replaced by
    the one call from the real pipeline that matters here
    (`securgraph/ingestion/service.py` resolves the "extract" adapter), so the
    assertion is that the policy loaded by the worker is what gates it.
    """
    hydrated_for: list[str | None] = []
    status_updates: list[dict[str, object]] = []

    async def fake_hydrate(session: object, tenant_id: str | None = None) -> int:
        hydrated_for.append(tenant_id)
        set_runtime_override("mrd", "egress_policy", BLOCK_EVERYTHING)
        return 1

    async def noop_set_tenant_context(*args: object, **kwargs: object) -> None:
        return None

    async def record_job_update(*args: object, **kwargs: object) -> None:
        status_updates.append(dict(kwargs))

    async def fake_ingest(*args: object, **kwargs: object) -> None:
        get_llm_adapter_for("extract", tenant_id="mrd")

    monkeypatch.setattr("securgraph.ingestion.worker.hydrate_overrides_from_db", fake_hydrate)
    monkeypatch.setattr(
        "securgraph.ingestion.worker.set_tenant_rls_context", noop_set_tenant_context
    )
    monkeypatch.setattr("securgraph.ingestion.worker.update_ingestion_job", record_job_update)
    monkeypatch.setattr("securgraph.ingestion.worker.ingest_document", fake_ingest)

    with pytest.raises(EgressBlockedError):
        await async_ingest_task(
            {"session_maker": _FakeSessionMaker()}, REQUEST_DICT, "ing-1", "trace-1"
        )

    # Scoped to the job's tenant, and done before the pipeline ran.
    assert hydrated_for == ["mrd"]
    failures = [update for update in status_updates if update.get("status") == "failed"]
    assert len(failures) == 1
    # expose_message=True on EgressBlockedError: the tenant sees the outcome of
    # the policy it configured.
    assert "Egress blocked by tenant policy" in str(failures[0]["error"])


async def test_without_the_reload_the_worker_would_not_see_the_policy() -> None:
    """The bug this guards, stated directly.

    The API process stores the override; a second process starts with an empty
    map, and `enforce_egress` reads that as no policy at all.
    """
    set_runtime_override("mrd", "egress_policy", BLOCK_EVERYTHING)
    with pytest.raises(EgressBlockedError):
        get_llm_adapter_for("extract", tenant_id="mrd")

    clear_runtime_overrides()  # a fresh process, e.g. the arq worker
    reset_llm_adapter_cache()
    # The same tenant and the same stored policy, and the adapter is built
    # without complaint. Nothing logs, nothing fails: the call just leaves.
    get_llm_adapter_for("extract", tenant_id="mrd")
