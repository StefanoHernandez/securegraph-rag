from __future__ import annotations

from typing import Any

import pytest

from securgraph.domain import Citation, GraphPath, RetrievalEvidence, RetrieverId
from securgraph.indexing.postgres_writer import ChunkCitationMetadata
from securgraph.orchestration.citations import build_authorized_citations

pytestmark = pytest.mark.unit


def _evidence(chunk_id: str, rank: int = 0) -> RetrievalEvidence:
    return RetrievalEvidence(
        evidence_id=f"ev_{chunk_id}",
        trace_id="trace-1",
        tenant_id="mrd-lab",
        evidence_type="chunk",
        object_id=chunk_id,
        retriever_id=RetrieverId.VECTOR,
        retrieval_strategy="dense",
        original_rank=rank,
        original_score=0.9,
        policy_check_id="dec-1",
    )


@pytest.mark.asyncio
async def test_build_authorized_citations_uses_db_source_uri_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_chunk_ids: list[str] = []

    async def fake_fetch(
        _session: Any, tenant_id: str, chunk_ids: list[str]
    ) -> dict[str, ChunkCitationMetadata]:
        assert tenant_id == "mrd-lab"
        requested_chunk_ids.extend(chunk_ids)
        return {
            "chunk-policy-1": ChunkCitationMetadata(
                chunk_id="chunk-policy-1",
                document_id="doc-policy",
                source_uri="documents/01_access_policy.md",
                title="Policy Accessi",
                page_start=1,
                page_end=2,
                ordinal=0,
            ),
            "chunk-policy-2": ChunkCitationMetadata(
                chunk_id="chunk-policy-2",
                document_id="doc-policy",
                source_uri="documents/01_access_policy.md",
                title="Policy Accessi",
                page_start=3,
                page_end=4,
                ordinal=1,
            ),
            "chunk-project-1": ChunkCitationMetadata(
                chunk_id="chunk-project-1",
                document_id="doc-project",
                source_uri="documents/08_project_plan.md",
                title="Project Plan",
                page_start=None,
                page_end=None,
                ordinal=0,
            ),
        }

    monkeypatch.setattr(
        "securgraph.orchestration.citations.fetch_chunk_citation_metadata", fake_fetch
    )

    citations = await build_authorized_citations(
        object(),
        tenant_id="mrd-lab",
        vector_context=[
            _evidence("chunk-policy-1", rank=0),
            _evidence("chunk-policy-2", rank=1),
        ],
        graph_context=[
            GraphPath(
                path_id="path-project",
                nodes=("Project", "Milestone"),
                edges=("edge-1",),
                source_chunk_ids=("chunk-project-1",),
            )
        ],
        trace_id="trace-1",
    )

    assert requested_chunk_ids == ["chunk-policy-1", "chunk-policy-2", "chunk-project-1"]
    assert [citation.source_uri for citation in citations] == [
        "documents/01_access_policy.md",
        "documents/08_project_plan.md",
    ]
    assert citations[0].document_id == "doc-policy"
    assert citations[0].chunk_id == "chunk-policy-1"
    assert citations[0].page_start == 1
    assert citations[1].graph_path_id == "path-project"


@pytest.mark.asyncio
async def test_build_authorized_citations_falls_back_to_evidence_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(
        _session: Any, _tenant_id: str, _chunk_ids: list[str]
    ) -> dict[str, ChunkCitationMetadata]:
        return {}

    monkeypatch.setattr(
        "securgraph.orchestration.citations.fetch_chunk_citation_metadata", fake_fetch
    )

    evidence = _evidence("chunk-fallback").model_copy(
        update={
            "document_id": "documents/15_private_architecture.md",
            "source_uri": "documents/15_private_architecture.md",
        }
    )
    citations = await build_authorized_citations(
        object(),
        tenant_id="mrd-lab",
        vector_context=[evidence],
        graph_context=[],
        trace_id="trace-1",
    )

    assert citations == (
        Citation(
            evidence_id="ev_chunk-fallback",
            document_id="documents/15_private_architecture.md",
            chunk_id="chunk-fallback",
            label="15_private_architecture.md",
            source_uri="documents/15_private_architecture.md",
        ),
    )
