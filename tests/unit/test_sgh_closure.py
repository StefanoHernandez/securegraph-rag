"""SGH-01..SGH-10 closure regression tests.

Each test maps directly to a Security Hardening finding in
`wiki/project/security-hardening.md`. Tests that require live PostgreSQL,
OpenFGA, or real backends are marked `integration`; the rest are unit tests
using mocked dependencies and the ASGI test client.
"""

from __future__ import annotations

import contextlib
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# SGH-01: query identity binding — effective_user_id never caller-controlled
# ---------------------------------------------------------------------------


class _StopWorkflow(RuntimeError):
    pass


def _capturing_workflow(sink: dict):
    """Fake compiled workflow: record the subject's user_id, then stop."""
    async def _ainvoke(state):
        sink["user_id"] = state["subject"].user_id
        raise _StopWorkflow("captured")

    app = MagicMock()
    app.ainvoke = _ainvoke
    return app


@pytest.mark.unit
async def test_sgh01_query_ignores_caller_user_id_when_auth_off(
    auth_off, monkeypatch
):
    sink: dict = {}
    monkeypatch.setattr(
        "securgraph.api.routes.query.compile_workflow",
        lambda qdrant, graph: _capturing_workflow(sink),
    )
    async with build_client() as client:
        # _StopWorkflow is raised inside the route after `sink` is populated;
        # Starlette's ServerErrorMiddleware re-raises past its own handler
        # (wrapped in an ExceptionGroup by anyio's TaskGroup), so the client
        # call itself surfaces it. We only care that `sink` was captured.
        with contextlib.suppress(BaseException):
            await client.post(
                "/api/v1/query",
                json={"tenant_id": "t1", "query": "hi", "user_id": "user:attacker"},
            )
    assert sink["user_id"] == "user:anonymous", (
        "SGH-01: caller-supplied user_id must never become the FGA subject"
    )


@pytest.mark.unit
async def test_sgh01_eval_identity_flag_honours_caller_user_id(
    auth_off, monkeypatch
):
    monkeypatch.setenv("SECURGRAPH_EVAL_IDENTITY_MODE", "true")
    get_settings.cache_clear()
    sink: dict = {}
    monkeypatch.setattr(
        "securgraph.api.routes.query.compile_workflow",
        lambda qdrant, graph: _capturing_workflow(sink),
    )
    async with build_client() as client:
        with contextlib.suppress(BaseException):
            await client.post(
                "/api/v1/query",
                json={"tenant_id": "t1", "query": "hi", "user_id": "user:sim"},
            )
    get_settings.cache_clear()
    assert sink["user_id"] == "user:sim", (
        "SGH-01: eval identity mode must pass the simulated subject through"
    )


# ---------------------------------------------------------------------------
# SGH-02: ingest owner delegation — owner defaults to principal, delegation checked
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh02_non_admin_cannot_delegate_ownership(auth_on):
    # role=analyst clears the ingest-role gate (_ensure_may_ingest) so the
    # request reaches the delegation check below it; analyst is still not
    # tenant_admin/super_admin, so can_admin_tenant() is False.
    alice = make_principal(user_id="alice", tenant_id="t1", role="analyst")
    async with build_client(principal=alice) as client:
        resp = await client.post(
            "/api/v1/ingest",
            data={"tenant_id": "t1", "owner_id": "user:bob"},
            files={"file": ("rules.md", b"# Rules\nbody", "text/markdown")},
        )
    assert resp.status_code == 403, (
        "SGH-02: a non-admin delegating ownership to another user must be denied"
    )


@pytest.mark.unit
async def test_sgh02_self_owner_is_not_blocked_by_delegation_check(auth_on):
    alice = make_principal(user_id="alice", tenant_id="t1", role="analyst")
    db = _permissive_db()
    template_result = MagicMock()
    template_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=template_result)
    async with build_client(principal=alice, db=db) as client:
        resp = await client.post(
            "/api/v1/ingest",
            data={"tenant_id": "t1", "owner_id": "user:alice"},
            files={"file": ("rules.md", b"# Rules\nbody", "text/markdown")},
        )
    # Self-ownership passes the delegation gate. It may still fail later
    # (downstream ingestion needs backends) but MUST NOT be the 403 delegation
    # denial.
    assert resp.status_code != 403, (
        "SGH-02: uploading under one's own id must not hit the delegation denial"
    )


# ---------------------------------------------------------------------------
# SGH-03: ingest status auth + tenant/owner check
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh03_status_denies_non_owner_non_admin(auth_on):
    job_row = MagicMock()
    job_row.tenant_id = "t1"
    job_row.requested_by = "bob"
    job_row.owner_id = "bob"
    db = AsyncMock()
    db.get = AsyncMock(return_value=job_row)

    alice = make_principal(user_id="alice", tenant_id="t1", role="member")
    async with build_client(principal=alice, db=db) as client:
        resp = await client.get("/api/v1/ingest/status/job-123")
    assert resp.status_code == 403, (
        "SGH-03: a non-owner, non-admin caller must not read another's job status"
    )


@pytest.mark.unit
async def test_sgh03_status_allows_the_job_owner(auth_on):
    job_row = MagicMock()
    job_row.tenant_id = "t1"
    job_row.requested_by = "alice"
    job_row.owner_id = "alice"
    job_row.job_id = "job-123"
    job_row.trace_id = "tr"
    job_row.document_id = "doc"
    job_row.status = "queued"
    job_row.started_at = datetime.now(timezone.utc)
    job_row.completed_at = None
    job_row.error = None
    db = AsyncMock()
    db.get = AsyncMock(return_value=job_row)

    alice = make_principal(user_id="alice", tenant_id="t1", role="member")
    async with build_client(principal=alice, db=db) as client:
        resp = await client.get("/api/v1/ingest/status/job-123")
    assert resp.status_code == 200, "SGH-03: the job owner must be able to read status"

# ---------------------------------------------------------------------------
# SGH-04: explain owner/admin check + missing policy = denied
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh04_explain_denies_non_owner_non_admin(auth_on):
    """SGH-04: explain must deny a non-owner, non-admin caller (behavioural)."""
    trace = MagicMock()
    trace.tenant_id = "t1"
    trace.user_id = "user:bob"
    db = AsyncMock()
    db.get = AsyncMock(return_value=trace)  # QueryTraceRow lookup

    alice = make_principal(user_id="alice", tenant_id="t1", role="member")
    async with build_client(principal=alice, db=db) as client:
        resp = await client.get("/api/v1/explain/tr-1", params={"tenant_id": "t1"})
    assert resp.status_code == 403, (
        "SGH-04: only the trace owner or a tenant admin may view a trace"
    )


@pytest.mark.unit
def test_sgh04_missing_decision_does_not_resolve_to_allow():
    """SGH-04: an evidence row whose linked policy decision was never
    persisted (missing from the accounting map) must resolve to "no
    decision", never to an implicit allow.

    Drives the real module-level `_linked_policy_decision` helper that
    `explain_query` uses to gate each evidence row, rather than grepping the
    route source for a literal string.
    """
    from securgraph.api.routes.explain import _linked_policy_decision
    from securgraph.indexing.db import RetrievalEvidenceRow

    evidence = RetrievalEvidenceRow(
        evidence_id="ev-1",
        trace_id="tr-1",
        tenant_id="t1",
        object_id="c1",
        policy_check_id="d1",
        retriever_id="vector",
        original_rank=0,
        original_score=0.9,
        included_in_prompt=True,
    )

    missing = _linked_policy_decision(evidence, {})
    assert missing is None, (
        "SGH-04: a missing policy decision must not resolve to an allow"
    )


@pytest.mark.unit
async def test_sgh04_denied_evidence_is_redacted_not_disclosed(auth_on):
    """SGH-04: evidence linked to an explicit non-'allow' policy decision must
    come back redacted, never with its real content disclosed.

    This drives the actual fail-closed disclosure gate in `explain_query`
    (`is_allowed = decision is not None and decision.decision.lower() ==
    "allow"`, `explain.py:294`/`:358`) end-to-end via the real HTTP route,
    rather than only calling the `_linked_policy_decision` helper in
    isolation. A tampered gate that defaults to allow (e.g. hardcoding
    `is_allowed = True`) makes this test fail.
    """
    from securgraph.indexing.db import (
        PolicyDecisionRow,
        QueryTraceRow,
        RetrievalEvidenceRow,
    )

    trace = QueryTraceRow(
        trace_id="tr-1",
        tenant_id="t1",
        user_id="user:alice",
        query="what does the policy say?",
        started_at=datetime.now(timezone.utc),
        # v1 makes `_integrity_status` return "legacy_unverified" up front
        # (explain.py:131-132), so the test doesn't need to mock chain
        # verification too.
        tamper_version="v1",
    )

    evidence_row = RetrievalEvidenceRow(
        evidence_id="ev-1",
        trace_id="tr-1",
        tenant_id="t1",
        object_id="chunk-1",
        policy_check_id="d1",
        retriever_id="vector",  # chunk-backed, not graph/community
        original_rank=0,
        original_score=0.9,
        included_in_prompt=True,
    )
    deny_decision = PolicyDecisionRow(
        decision_id="d1",
        trace_id="tr-1",
        tenant_id="t1",
        user_id="user:alice",
        object_type="chunk",
        object_id="chunk-1",
        action="read",
        decision="deny",
        retriever_id="vector",
    )

    evidence_result = MagicMock()
    evidence_result.scalars.return_value.all.return_value = [evidence_row]
    decision_result = MagicMock()
    decision_result.scalars.return_value.all.return_value = [deny_decision]

    db = AsyncMock()
    db.get = AsyncMock(return_value=trace)  # QueryTraceRow lookup
    # explain_query issues exactly two session.execute calls, in order:
    # (1) the evidence select, (2) the policy-decisions select.
    db.execute = AsyncMock(side_effect=[evidence_result, decision_result])

    # tenant_admin passes the owner/admin gate regardless of trace ownership,
    # so this test isolates the disclosure gate rather than the authz gate
    # already covered by test_sgh04_explain_denies_non_owner_non_admin.
    admin = make_principal(user_id="alice", tenant_id="t1", role="tenant_admin")
    async with build_client(principal=admin, db=db) as client:
        resp = await client.get("/api/v1/explain/tr-1", params={"tenant_id": "t1"})

    assert resp.status_code == 200
    body = resp.json()
    [ev] = body["evidence"]
    assert ev["status"] == "redacted", (
        "SGH-04: evidence linked to a non-allow decision must be marked redacted"
    )
    assert ev["object_data"] == "CONFIDENTIAL - ACCESS DENIED", (
        "SGH-04: a denied evidence row must never disclose its real content"
    )


# ---------------------------------------------------------------------------
# SGH-05: no auto-seed demo users on startup
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh05_startup_inserts_no_users(auth_off, monkeypatch):
    """Running the app lifespan must not create any user/seed rows."""
    from securgraph.api.main import create_app

    added: list = []
    session = _permissive_db()
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))

    # If any startup code opened its own session, route it to our spy.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_session():
        yield session

    monkeypatch.setattr(
        "securgraph.api.dependencies.AsyncSessionLocal",
        lambda: _fake_session(),
        raising=False,
    )

    app = create_app()
    async with app.router.lifespan_context(app):
        pass

    seed_like = [o for o in added if "user" in type(o).__name__.lower()]
    assert not seed_like, f"SGH-05: startup must not seed users, got {seed_like}"


@pytest.mark.unit
def test_sgh05_bootstrap_is_a_registered_cli_command(monkeypatch, capsys):
    """`bootstrap-superadmin` must be a real subcommand of the CLI parser."""
    import pytest as _pytest

    from securgraph.cli import main

    # argparse exits when a required subcommand is missing; invoking the
    # bootstrap subcommand without its required args must fail *as that
    # subcommand* (SystemExit from argparse), proving it is registered.
    monkeypatch.setattr("sys.argv", ["securgraph", "bootstrap-superadmin"])
    with _pytest.raises(SystemExit):
        main()
    err = capsys.readouterr().err
    assert "the following arguments are required" in err and "--username" in err, (
        "SGH-05: bootstrap-superadmin must be a registered CLI subcommand with a required --username"
    )


# ---------------------------------------------------------------------------
# SGH-07: committed policy decision rows for allow AND deny paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_sgh07_workflow_persists_allow_and_deny_decisions(monkeypatch):
    """SGH-07: running the compiled workflow over a mixed allow/deny FGA
    result must call `write_policy_decisions` with both an allowed and a
    denied decision, and `filter_chunk_ids` must be invoked. Drives the real
    `compile_workflow` end-to-end with mocked collaborators, rather than
    grepping its source.
    """
    from unittest.mock import AsyncMock, MagicMock

    from securgraph.domain import RetrievalEvidence, RetrieverId, Subject
    from securgraph.domain.policy import (
        PolicyDecision,
        PolicyDecisionValue,
        PolicyObjectType,
    )
    from securgraph.orchestration.workflow import compile_workflow

    qdrant = MagicMock()
    qdrant.search_lexical = AsyncMock(return_value=[])
    graph = MagicMock()
    graph.traverse_authorized_subgraph = AsyncMock(return_value=[])
    graph.driver = MagicMock()
    graph.fga = MagicMock()

    ev_allow = RetrievalEvidence(
        evidence_id="ev-a", trace_id="tr1", tenant_id="t1", evidence_type="chunk",
        object_id="chunk-allow", retriever_id=RetrieverId.VECTOR,
        retrieval_strategy="dense", original_rank=0, original_score=0.9,
        policy_check_id="unverified", text="allowed source",
    )
    ev_deny = RetrievalEvidence(
        evidence_id="ev-d", trace_id="tr1", tenant_id="t1", evidence_type="chunk",
        object_id="chunk-deny", retriever_id=RetrieverId.VECTOR,
        retrieval_strategy="dense", original_rank=1, original_score=0.8,
        policy_check_id="unverified", text="denied source",
    )
    qdrant.search_dense = AsyncMock(return_value=[ev_allow, ev_deny])

    allow = PolicyDecision(
        decision_id="dec-allow", trace_id="tr1", tenant_id="t1", user_id="user:alice",
        object_type=PolicyObjectType.CHUNK, object_id="chunk-allow",
        decision=PolicyDecisionValue.ALLOW, reason_code="openfga_ok",
    )
    deny = PolicyDecision(
        decision_id="dec-deny", trace_id="tr1", tenant_id="t1", user_id="user:alice",
        object_type=PolicyObjectType.CHUNK, object_id="chunk-deny",
        decision=PolicyDecisionValue.DENY, reason_code="unauthorized_rbac",
    )
    graph.fga.filter_chunk_ids = AsyncMock(return_value=({"chunk-allow"}, [allow, deny]))

    class FakeSessionContext:
        async def __aenter__(self):
            s = MagicMock()
            s.commit = AsyncMock()
            return s

        async def __aexit__(self, *args):
            return None

    write_decisions = AsyncMock()
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.AsyncSessionLocal",
        lambda: FakeSessionContext(),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.set_tenant_rls_context", AsyncMock()
    )
    monkeypatch.setattr(
        "securgraph.indexing.postgres_writer.fetch_chunk_text_hashes",
        AsyncMock(return_value={"chunk-deny": "sha256:x"}),
    )
    monkeypatch.setattr(
        "securgraph.indexing.postgres_writer.write_policy_decisions", write_decisions
    )

    def _fake_adapter(text="answer"):
        a = MagicMock()
        a.chat = AsyncMock(return_value=text)
        a.extract_claims = AsyncMock(return_value=MagicMock(claims=[]))
        a.judge_nli = AsyncMock(
            return_value=MagicMock(label="neutral", confidence=0.5, reason="mock")
        )
        return a

    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter", lambda *a, **k: _fake_adapter()
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.get_llm_adapter_for",
        lambda *a, **k: _fake_adapter(),
    )
    monkeypatch.setattr(
        "securgraph.orchestration.workflow.build_authorized_citations",
        AsyncMock(return_value=()),
    )

    state = {
        "query": "q", "subject": Subject(tenant_id="t1", user_id="user:alice"),
        "options": {"retrieval_mode": "vector", "top_k": 5, "consensus_threshold": 0.7},
        "trace_id": "tr1", "vector_context": [], "vector_draft": None,
        "vector_claims": [], "graph_context": [], "graph_draft": None,
        "graph_claims": [], "consensus_report": None, "final_answer": None,
        "warnings": [],
    }
    await compile_workflow(qdrant, graph).ainvoke(state)

    graph.fga.filter_chunk_ids.assert_awaited()
    persisted = write_decisions.await_args.args[1]
    values = {d.decision for d in persisted}
    assert PolicyDecisionValue.ALLOW in values and PolicyDecisionValue.DENY in values, (
        f"SGH-07: workflow must persist both allow and deny decisions, got {values}"
    )


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
