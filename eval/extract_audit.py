"""Empirical audit of graph extraction quality across model + thinking combos.

Reads the first ingested chunk for the baseline tenant (assumed: corpus
01_internal_policy.txt about Orion-7 / Maria Conti / Helios) and runs the
exact JSON extraction prompt from ``securgraph.adapters.ollama.extract_graph_slice``
against each (model, thinking) combination. Reports entities, relations,
latency, and a quick rubric score so the production extractor is chosen on
evidence rather than vibes.

This is a one-off engineering audit, not a recurring test. Lives under eval/
because it shares the isolated environment, but does not touch RAGAS.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from ollama import AsyncClient
from sqlalchemy import text

from securgraph.api.dependencies import AsyncSessionLocal

OLLAMA_URL = "http://localhost:11434"
NUM_CTX = 4096
TENANT = "mrd"

EXPECTED_ENTITIES = {
    "maria conti",
    "anya volkov",
    "orion-7",
    "orion 7",
    "helios",
    "project lead",
    "vp of data operations",
    "vienna",
}

EXTRACT_PROMPT_SYSTEM = (
    "You are an expert knowledge graph extraction agent. Analyze the provided text chunk and "
    "extract all entities and their semantic relationships.\n"
    "Output strictly in JSON matching this schema:\n"
    "{\n"
    '  "entities": [{"name": "entity_name", "label": "ENTITY_TYPE"}],\n'
    '  "relations": [{"source_entity_name": "name", "target_entity_name": "name", '
    '"relationship_type": "TYPE", "confidence": 0.9}]\n'
    "}\n"
    "If no entities/relations are found, return empty lists. Do not write "
    "markdown blocks or text, return ONLY the raw JSON."
)


async def get_chunk_text() -> str:
    async with AsyncSessionLocal() as s:
        r = await s.execute(
            text("SELECT text FROM chunks WHERE tenant_id=:tid ORDER BY ordinal LIMIT 1"),
            {"tid": TENANT},
        )
        row = r.scalar()
        if not row:
            raise RuntimeError(
                f"No chunks found for tenant_id={TENANT}. Ingest a corpus doc first."
            )
        return row


def score_extraction(slice_data: dict, latency_s: float) -> dict[str, Any]:
    entities = slice_data.get("entities") or []
    relations = slice_data.get("relations") or []
    ent_names = {(e.get("name") or "").strip().lower() for e in entities}
    hit_keys = {k for k in EXPECTED_ENTITIES if any(k in n or n in k for n in ent_names)}
    recall = len(hit_keys) / len(EXPECTED_ENTITIES)
    return {
        "n_entities": len(entities),
        "n_relations": len(relations),
        "expected_entity_recall": round(recall, 3),
        "expected_hits": sorted(hit_keys),
        "latency_s": round(latency_s, 1),
        "entities": [(e.get("name"), e.get("label")) for e in entities],
        "relations_sample": [
            (
                r.get("source_entity_name"),
                r.get("target_entity_name"),
                r.get("relationship_type"),
                r.get("confidence"),
            )
            for r in relations[:8]
        ],
    }


async def extract_one(model: str, thinking: bool, chunk_text: str) -> dict[str, Any]:
    client = AsyncClient(host=OLLAMA_URL)
    t0 = time.monotonic()
    try:
        resp = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACT_PROMPT_SYSTEM},
                {"role": "user", "content": chunk_text},
            ],
            format="json",
            options={"num_ctx": NUM_CTX},
            think=thinking,
        )
        latency = time.monotonic() - t0
        content = (resp.message.content or "{}").strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return {
                "error": f"json_decode: {e}",
                "raw_head": content[:300],
                "latency_s": round(latency, 1),
            }
        return score_extraction(data, latency)
    except Exception as e:
        return {"error": str(e), "latency_s": round(time.monotonic() - t0, 1)}


async def main() -> None:
    chunk_text = await get_chunk_text()
    print(f"chunk text len: {len(chunk_text)} chars\n")
    print(f"first 200 chars: {chunk_text[:200]!r}\n")

    combos = [
        ("nemotron-3-nano:4b", False),
        ("nemotron-3-nano:4b", True),
        ("gemma4:e2b-it-qat", False),
        ("gemma4:e2b-it-qat", True),
        ("granite4.1:3b", False),
        ("qwen3.5:2b", False),
        ("qwen3.5:2b", True),
    ]

    results: dict[str, dict[str, Any]] = {}
    for model, thinking in combos:
        key = f"{model} think={thinking}"
        print(f"--- {key} ---")
        res = await extract_one(model, thinking, chunk_text)
        results[key] = res
        if "error" in res:
            print(f"  ERROR ({res['latency_s']}s): {res['error']}")
            if "raw_head" in res:
                print(f"  raw head: {res['raw_head']!r}")
        else:
            print(
                f"  {res['latency_s']}s | entities={res['n_entities']} "
                f"relations={res['n_relations']} "
                f"recall={res['expected_entity_recall']}"
            )
            print(f"  hits: {res['expected_hits']}")
            print(f"  ents: {res['entities']}")
            for rel in res["relations_sample"]:
                print(f"    rel: {rel}")
        print()

    print("=== summary ===")
    print(f"  expected entities ({len(EXPECTED_ENTITIES)}): {sorted(EXPECTED_ENTITIES)}")
    ranked = sorted(
        [(k, v) for k, v in results.items() if "error" not in v],
        key=lambda kv: (
            -kv[1]["expected_entity_recall"],
            -kv[1]["n_relations"],
            kv[1]["latency_s"],
        ),
    )
    for k, v in ranked:
        print(
            f"  {k:35s} recall={v['expected_entity_recall']:.2f} "
            f"rel={v['n_relations']:>2} ent={v['n_entities']:>2} "
            f"lat={v['latency_s']:.1f}s"
        )
    for k, v in results.items():
        if "error" in v:
            print(f"  {k:35s} ERROR: {v['error'][:80]}")


if __name__ == "__main__":
    asyncio.run(main())
