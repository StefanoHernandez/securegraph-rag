"""Smoke tests for domain models.

These tests are pure and require no backends. They prove the Pydantic schemas
parse, frozen instances are immutable, and basic invariants hold.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from securgraph.domain import (
    Chunk,
    Citation,
    Classification,
    ConsensusReport,
    Document,
    GraphEdge,
    GraphNode,
    GraphPath,
    PolicyDecision,
    PolicyDecisionValue,
    PolicyObjectType,
    QueryClassification,
    QueryRequest,
    QueryScope,
    RetrievalEvidence,
    RetrieverId,
    Subject,
    utcnow,
)

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------------
# Subject / scope
# ----------------------------------------------------------------------------


def test_subject_normalizes_fga_user_with_colon() -> None:
    s = Subject(user_id="user:alice", tenant_id="mrd")
    assert s.fga_user == "user:alice"


def test_subject_prefixes_bare_user_id() -> None:
    s = Subject(user_id="alice", tenant_id="mrd")
    assert s.fga_user == "user:alice"


def test_query_scope_uses_the_backend_classification_enum() -> None:
    scope = QueryScope(
        max_classification=Classification.CONFIDENTIAL,
        document_ids=("doc-1",),
    )

    assert scope.max_classification is Classification.CONFIDENTIAL
    with pytest.raises(ValidationError):
        QueryRequest(
            tenant_id="mrd",
            user_id="user:alice",
            query="Scoped query",
            scope_max_classification="top_secret",
        )


# ----------------------------------------------------------------------------
# Document / chunk
# ----------------------------------------------------------------------------


def test_document_defaults_and_immutability() -> None:
    d = Document(
        document_id="doc_001",
        tenant_id="mrd",
        source_uri="file:///x.pdf",
        title="Policy",
        owner_id="user:stefano",
        source_hash="sha256:abc",
    )
    assert d.classification is Classification.INTERNAL
    assert d.language == "en"
    with pytest.raises(ValidationError):
        # frozen=True ⇒ mutation rejected
        d.title = "other"  # type: ignore[misc]


def test_chunk_requires_text_and_token_count() -> None:
    c = Chunk(
        chunk_id="c_001",
        document_id="doc_001",
        tenant_id="mrd",
        text="some text",
        token_count=2,
        ordinal=0,
        text_hash="sha256:def",
    )
    assert c.text == "some text"
    assert c.policy_tags == ()


def test_chunk_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        Chunk(
            chunk_id="c_002",
            document_id="doc_001",
            tenant_id="mrd",
            text="",
            token_count=0,
            ordinal=0,
            text_hash="sha256:000",
        )


# ----------------------------------------------------------------------------
# Graph
# ----------------------------------------------------------------------------


def test_graph_node_requires_source_chunks() -> None:
    n = GraphNode(
        node_id="n1",
        tenant_id="mrd",
        label="System",
        name="System A",
        source_chunk_ids=("c_001",),
        confidence=0.9,
    )
    assert n.aliases == ()


def test_graph_node_rejects_missing_source_chunks() -> None:
    with pytest.raises(ValidationError):
        GraphNode(
            node_id="n2",
            tenant_id="mrd",
            label="System",
            name="B",
            source_chunk_ids=(),
            confidence=0.5,
        )


def test_graph_path_validates_edges_count() -> None:
    GraphPath(path_id="p1", nodes=("n1", "n2"), edges=("e1",), confidence=0.9)
    with pytest.raises(ValueError, match="edges count"):
        GraphPath(path_id="p2", nodes=("n1", "n2"), edges=("e1", "e2"))


def test_graph_edge_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        GraphEdge(
            edge_id="e_bad",
            tenant_id="mrd",
            source_node_id="n1",
            target_node_id="n2",
            relationship_type="depends_on",
            source_chunk_ids=("c1",),
            confidence=1.5,
        )


# ----------------------------------------------------------------------------
# Policy + retrieval
# ----------------------------------------------------------------------------


def test_policy_decision_records_deny() -> None:
    pd = PolicyDecision(
        decision_id="pd1",
        trace_id="t1",
        tenant_id="mrd",
        user_id="user:bob",
        object_type=PolicyObjectType.CHUNK,
        object_id="c_001",
        decision=PolicyDecisionValue.DENY,
        reason_code="not_project_member",
    )
    assert pd.decision is PolicyDecisionValue.DENY
    assert pd.policy_engine == "openfga"


def test_retrieval_evidence_carries_channel_and_policy_check() -> None:
    ev = RetrievalEvidence(
        evidence_id="ev_1",
        trace_id="t1",
        tenant_id="mrd",
        evidence_type="chunk",
        object_id="c_001",
        retriever_id=RetrieverId.VECTOR,
        retrieval_strategy="dense",
        original_rank=0,
        original_score=0.92,
        policy_check_id="pd1",
    )
    assert ev.retriever_id is RetrieverId.VECTOR
    assert ev.included_in_prompt is False


# ----------------------------------------------------------------------------
# Query API + consensus
# ----------------------------------------------------------------------------


def test_query_request_defaults() -> None:
    q = QueryRequest(tenant_id="mrd", user_id="user:alice", query="hello world")
    assert q.options.retrieval_mode == "auto"
    assert q.options.max_graph_depth == 2
    assert q.options.consensus_threshold == 0.7  # M4.5


def test_consensus_report_bounds() -> None:
    cr = ConsensusReport(
        trace_id="t1",
        entity_overlap_jaccard=0.8,
        claim_nli_score=0.9,
        agreement_score=0.86,
        high_trust=True,
    )
    assert cr.high_trust is True


def test_consensus_score_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ConsensusReport(
            trace_id="t1",
            entity_overlap_jaccard=1.1,
            claim_nli_score=0.5,
            agreement_score=0.5,
        )


# ----------------------------------------------------------------------------
# Misc
# ----------------------------------------------------------------------------


def test_utcnow_returns_aware() -> None:
    now = utcnow()
    assert now.tzinfo is not None
    assert now.tzinfo.utcoffset(now) is not None


def test_citation_minimal() -> None:
    c = Citation(evidence_id="ev_1", label="Policy §2.1")
    assert c.label == "Policy §2.1"


def test_query_classification_values() -> None:
    assert QueryClassification.FACTUAL_SEMANTIC.value == "factual_semantic"
