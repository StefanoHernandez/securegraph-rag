from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from securgraph.adapters.neo4j_writer import write_graph_slice
from securgraph.domain import Chunk, Classification
from securgraph.domain.graph import ExtractedGraphSlice

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_chunk_write_sets_owner_and_direct_viewers() -> None:
    captured: list[dict[str, Any]] = []

    async def fake_execute_write(fn, **kwargs):  # type: ignore[no-untyped-def]
        class Tx:
            async def run(self, cypher: str, **params: Any) -> Any:
                captured.append({"cypher": cypher, "params": params})
                return MagicMock()

        await fn(Tx(), **kwargs)

    session = MagicMock()
    session.execute_write = AsyncMock(side_effect=fake_execute_write)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    driver = MagicMock()
    driver.session = lambda: session

    chunk = Chunk(
        chunk_id="c1",
        document_id="d1",
        tenant_id="t1",
        text="hello",
        classification=Classification.INTERNAL,
        policy_tags=("eng",),
        token_count=1,
        ordinal=0,
        text_hash="abcdefgh",
    )
    await write_graph_slice(
        driver,
        tenant_id="t1",
        chunk=chunk,
        graph_slice=ExtractedGraphSlice(),
        owner_id="alice",
        direct_viewers=("carol",),
    )

    chunk_calls = [c for c in captured if "MERGE (c:Chunk" in c["cypher"]]
    assert chunk_calls, "expected a Chunk MERGE call"
    params = chunk_calls[0]["params"]
    assert params["owner_id"] == "alice"
    assert params["direct_viewers"] == ["carol"]
