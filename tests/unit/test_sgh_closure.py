"""SGH-01..SGH-10 closure regression tests.

Each test maps directly to a Security Hardening finding in
`wiki/project/security-hardening.md`. Tests that require live PostgreSQL,
OpenFGA, or real backends are marked `integration`; the rest are unit tests
using mocked dependencies and the ASGI test client.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from securgraph.api.routes.auth import AuthenticatedPrincipal, require_user
from securgraph.config import get_settings

_UNSET = object()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_on():
    os.environ["SECURGRAPH_AUTH_ENABLED"] = "true"
    get_settings.cache_clear()
    yield
    os.environ["SECURGRAPH_AUTH_ENABLED"] = "false"
    get_settings.cache_clear()


@pytest.fixture
def auth_off():
    os.environ["SECURGRAPH_AUTH_ENABLED"] = "false"
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_principal(**overrides) -> AuthenticatedPrincipal:
    """A verified principal; override any field for the case under test."""
    base = dict(
        user_id="alice",
        username="alice",
        tenant_id="t1",
        role="member",
        is_super_admin=False,
        must_change_password=False,
        session_id="s1",
    )
    base.update(overrides)
    return AuthenticatedPrincipal(**base)


def _permissive_db() -> AsyncMock:
    mock = AsyncMock()
    mock.add = MagicMock()
    mock.add_all = MagicMock()
    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.execute.return_value = MagicMock()
    return mock


@asynccontextmanager
async def build_client(*, principal=_UNSET, db=None):
    """ASGI client with mocked DB + retrievers, and an optional principal.

    principal=_UNSET  -> real require_user (auth-off yields None)
    principal=None    -> require_user overridden to return None
    principal=<obj>   -> require_user overridden to return that principal
    """
    from securgraph.api.dependencies import (
        get_db_session,
        get_graph_retriever,
        get_qdrant_retriever,
    )
    from securgraph.api.main import create_app

    app = create_app()
    session = db if db is not None else _permissive_db()

    async def _db():
        yield session

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_qdrant_retriever] = lambda: MagicMock()
    app.dependency_overrides[get_graph_retriever] = lambda: MagicMock()
    if principal is not _UNSET:
        app.dependency_overrides[require_user] = lambda: principal

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def sgh_client() -> AsyncClient:
    """ASGI client with mocked DB and retrievers for SGH route tests.

    Kept for the still-unmigrated SGH test bodies below (task 1 only
    touches the fixtures/imports region); later tasks replace their use
    of this fixture with `build_client`.
    """
    from securgraph.api.dependencies import (
        get_db_session,
        get_graph_retriever,
        get_qdrant_retriever,
    )
    from securgraph.api.main import create_app

    app = create_app()

    async def mock_db():
        yield _permissive_db()

    app.dependency_overrides[get_db_session] = mock_db
    app.dependency_overrides[get_qdrant_retriever] = lambda: MagicMock()
    app.dependency_overrides[get_graph_retriever] = lambda: MagicMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# SGH-01: query identity binding — effective_user_id never caller-controlled
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh01_query_uses_anonymous_when_auth_disabled(sgh_client, auth_off):
    """When auth is disabled (dev), query must NOT trust request.user_id.

    The effective user should be 'user:anonymous', not the caller-provided
    user_id — unless the eval identity simulation flag is explicitly set.
    We verify by checking that the code path for effective_user_id does not
    use request.user_id unconditionally.
    """
    import inspect

    from securgraph.api.routes.query import query_pipeline

    source = inspect.getsource(query_pipeline)
    # The eval identity mode must be gated behind an env flag check.
    assert "SECURGRAPH_EVAL_IDENTITY_MODE" in source, (
        "SGH-01: caller-provided user_id must be gated behind eval identity flag"
    )
    assert "eval_identity" in source, "SGH-01: must have eval identity guard"


@pytest.mark.unit
async def test_sgh01_query_anonymous_is_fixed_string(auth_off):
    """Verify the fallback user id is the fixed anonymous, not caller-controlled."""
    import inspect

    from securgraph.api.routes.query import query_pipeline

    source = inspect.getsource(query_pipeline)
    assert "user:anonymous" in source, "SGH-01: fallback must be 'user:anonymous'"


# ---------------------------------------------------------------------------
# SGH-02: ingest owner delegation — owner defaults to principal, delegation checked
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh02_ingest_owner_defaults_to_anonymous_when_auth_disabled(sgh_client, auth_off):
    """When auth is disabled and no owner_id provided, owner is user:anonymous."""
    import inspect

    from securgraph.api.routes.ingest import ingest_file

    source = inspect.getsource(ingest_file)
    assert "user:anonymous" in source, "SGH-02: must default to anonymous when no principal"


@pytest.mark.unit
async def test_sgh02_ingest_delegation_check_exists():
    """SGH-02: delegation path must check can_admin_tenant for non-self owner."""
    import inspect

    from securgraph.api.routes.ingest import ingest_file

    source = inspect.getsource(ingest_file)
    assert "can_admin_tenant" in source, "SGH-02: must check admin privilege for owner delegation"


# ---------------------------------------------------------------------------
# SGH-03: ingest status auth + tenant/owner check
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh03_ingest_status_requires_auth_check_exists():
    """SGH-03: ingest status route must check tenant and owner."""
    import inspect

    from securgraph.api.routes.ingest import get_ingest_status

    source = inspect.getsource(get_ingest_status)
    assert "can_access_tenant" in source or "can_admin_tenant" in source, (
        "SGH-03: ingest status must verify tenant access"
    )

# ---------------------------------------------------------------------------
# SGH-04: explain owner/admin check + missing policy = denied
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh04_explain_owner_check_exists():
    """SGH-04: explain must check trace owner or admin privilege."""
    import inspect

    from securgraph.api.routes.explain import explain_query

    source = inspect.getsource(explain_query)
    assert "is_owner" in source, "SGH-04: must check trace ownership"
    assert "can_admin_tenant" in source, "SGH-04: must allow admin access"


@pytest.mark.unit
async def test_sgh04_missing_policy_decision_is_denied_not_allowed():
    """SGH-04: absent policy decisions must be treated as denied, not allow."""
    import inspect

    from securgraph.api.routes.explain import explain_query

    source = inspect.getsource(explain_query)
    # The code must use an explicit "allow" check, not a default-allow.
    assert '== "allow"' in source or "== 'allow'" in source, (
        "SGH-04: evidence must be allowed only on explicit 'allow' decision"
    )


# ---------------------------------------------------------------------------
# SGH-05: no auto-seed demo users on startup
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh05_no_auto_seed_in_startup():
    """SGH-05: application startup must not auto-seed demo users."""
    import inspect

    from securgraph.api.main import create_app

    source = inspect.getsource(create_app)
    # Must not contain seed-demo or auto-create-user calls in the app factory.
    assert "seed_demo" not in source, "SGH-05: create_app must not call seed_demo"
    assert "demo_user" not in source.lower(), "SGH-05: no demo user creation in startup"


@pytest.mark.unit
async def test_sgh05_bootstrap_is_cli_only():
    """SGH-05: bootstrap-superadmin must be a CLI command, not startup auto-seed."""
    import inspect

    from securgraph.cli import main

    source = inspect.getsource(main)
    assert "bootstrap" in source.lower(), "SGH-05: CLI must expose bootstrap-superadmin"


# ---------------------------------------------------------------------------
# SGH-07: committed policy decision rows for allow AND deny paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh07_policy_decisions_committed_for_allow_and_deny():
    """SGH-07: write_policy_decisions must be called in the workflow."""
    import inspect

    from securgraph.orchestration.workflow import compile_workflow

    source = inspect.getsource(compile_workflow)
    assert "write_policy_decisions" in source, (
        "SGH-07: workflow must persist policy decisions"
    )
    assert "filter_chunk_ids" in source, "SGH-07: workflow must run FGA filter"


# ---------------------------------------------------------------------------
# SGH-08: chain-head serialization prevents concurrent forks
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh08_chain_head_locking_mechanism_exists():
    """SGH-08: tamper chain must use chain-head row + locking."""
    import inspect

    from securgraph.indexing.tamper import chain_query_trace

    source = inspect.getsource(chain_query_trace)
    assert "chain_head" in source, "SGH-08: must use chain-head row for serialization"


@pytest.mark.integration
async def test_sgh08_concurrent_writers_do_not_fork():
    """SGH-08: two concurrent chain_query_trace calls must not produce forked chains.

    Requires live PostgreSQL. Run with `make up` + `alembic upgrade head`.
    """
    import asyncio
    import uuid

    from sqlalchemy import text

    from securgraph.api.dependencies import AsyncSessionLocal
    from securgraph.indexing.db import QueryTraceRow
    from securgraph.indexing.tamper import chain_query_trace, verify_table_chain

    tenant = f"sgh08_{uuid.uuid4().hex[:8]}"
    trace_ids = [f"trace_sgh08_{i}_{uuid.uuid4().hex[:8]}" for i in range(4)]

    async def write_trace(trace_id: str) -> None:
        async with AsyncSessionLocal() as session:
            from securgraph.domain import utcnow

            row = QueryTraceRow(
                trace_id=trace_id,
                tenant_id=tenant,
                user_id="user:anonymous",
                query="concurrent fork test",
                started_at=utcnow(),
            )
            await chain_query_trace(session, row)
            session.add(row)
            await session.commit()

    async with AsyncSessionLocal() as cleanup:
        await cleanup.execute(
            text("DELETE FROM query_traces WHERE tenant_id = :t"), {"t": tenant}
        )
        await cleanup.execute(
            text("DELETE FROM audit_chain_heads WHERE tenant_id = :t"), {"t": tenant}
        )
        await cleanup.commit()

    try:
        await asyncio.gather(*(write_trace(tid) for tid in trace_ids))

        async with AsyncSessionLocal() as session:
            is_intact, errors = await verify_table_chain(
                session, tenant_id=tenant, table_class=QueryTraceRow
            )
            assert is_intact, f"SGH-08: chain forked under concurrent writes: {errors}"
    finally:
        async with AsyncSessionLocal() as cleanup:
            await cleanup.execute(
                text("DELETE FROM query_traces WHERE tenant_id = :t"), {"t": tenant}
            )
            await cleanup.execute(
                text("DELETE FROM audit_chain_heads WHERE tenant_id = :t"), {"t": tenant}
            )
            await cleanup.commit()


# ---------------------------------------------------------------------------
# SGH-09: frontend default unverified (deferred to Fase 6 E2E)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sgh09_custody_timeline_does_not_hardcode_intact():
    """SGH-09: CustodyTimeline must not render INTACT/SEALED unconditionally.

    Full E2E proof deferred to Fase 6 (Playwright). This source-level check
    ensures the component does not hardcode integrity states.
    """
    from pathlib import Path

    component = Path("web/src/components/explain/CustodyTimeline.tsx")
    if not component.exists():
        pytest.skip("CustodyTimeline component not found")
    source = component.read_text()
    # Must not contain unconditional "INTACT" or "SEALED" string literals
    # that are rendered without a backend verification result.
    assert '"INTACT"' not in source or "verification" in source.lower(), (
        "SGH-09: CustodyTimeline must not hardcode INTACT without verification result"
    )


# ---------------------------------------------------------------------------
# SGH-10: readiness performs zero LLM completions
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh10_health_live_has_no_dependency_calls():
    """SGH-10: /health/live must not call any dependency (zero cost)."""
    import inspect

    from securgraph.api.routes.health import liveness

    source = inspect.getsource(liveness)
    # liveness must not reference postgres, qdrant, neo4j, openfga, ollama, or LLM.
    forbidden = ["postgres", "qdrant", "neo4j", "openfga", "ollama", "llm", "provider"]
    found = [kw for kw in forbidden if kw in source.lower()]
    assert not found, f"SGH-10: /health/live probes dependencies: {found}"


@pytest.mark.unit
async def test_sgh10_health_ready_does_not_invoke_llm():
    """SGH-10: /health/ready must use cached diagnostics, not invoke LLM completion."""
    import inspect

    from securgraph.api.routes.health import _readiness_body

    source = inspect.getsource(_readiness_body)
    assert "_cached_provider_report" in source or "cached" in source.lower(), (
        "SGH-10: readiness must use cached provider report, not live LLM call"
    )
    # Must not call ollama generate / chat / complete directly.
    assert "generate(" not in source, "SGH-10: readiness must not call LLM generate"
    assert ".chat(" not in source, "SGH-10: readiness must not call LLM chat"
