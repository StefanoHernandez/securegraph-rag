"""Rigorous extract-quality audit across model x thinking x doc x run.

Earlier audits ran a single chunk a single time and produced a noisy verdict
(gemma4 returned 16 relations one run, 0 the next). This v2 measures variance
properly: each (model, thinking) combination is called 3 times on each of the
5 baseline corpus documents, against a ground-truth entity list curated by
hand from the source text.

For each (model, thinking) we report mean +/- stdev of:
  * entity recall (against ground-truth)
  * relation count
  * latency

plus the JSON-parse success rate.

This is offline research code — slow by design (75+ LLM calls). Output is
written to ``eval/results/extract_audit_<UTC>.json`` for the paper.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import statistics
import time
from pathlib import Path
from typing import Any

from ollama import AsyncClient

OLLAMA_URL = "http://localhost:11434"
NUM_CTX = 4096
RUNS_PER_COMBO = 3
CORPUS_DIR = Path(__file__).parent.parent / "corpus" / "baseline_v1" / "documents"
RESULTS_DIR = Path(__file__).parent / "results"

# Hand-curated entity ground-truth per document. We match case-insensitively
# and tolerate substring overlap (so "Project Orion-7" matches "orion-7" and
# "orion 7"). Entities are organisations, persons, roles, locations, systems,
# regulations, and a few key dates.
GROUND_TRUTH: dict[str, set[str]] = {
    "01_internal_policy.txt": {
        "helios group",
        "orion-7",
        "maria conti",
        "tomas becker",
        "anya volkov",
        "vp of data operations",
        "data protection officer",
        "data steward",
        "vienna",
        "orion7-engineering",
        "tier-2 privacy training",
    },
    "02_service_contract.txt": {
        "helios group",
        "polaris systems",
        "luca ferrari",
        "maria conti",
        "orion-7",
        "graphvault",
        "master service agreement",
        "data protection officer",
    },
    "03_security_manual.txt": {
        "helios group",
        "graphvault",
        "hermesdb",
        "atlasmq",
        "restricted",
        "tier-2 privacy training",
        "data steward",
        "data protection officer",
        "aes-256-gcm",
        "tls 1.3",
        "orion-7",
    },
    "04_meeting_minutes.txt": {
        "maria conti",
        "tomas becker",
        "anya volkov",
        "pietro russo",
        "luca ferrari",
        "sara klein",
        "helios group",
        "polaris systems",
        "graphvault",
        "orion7-engineering",
        "vienna",
        "severity-2",
    },
    "05_regulation_excerpt.txt": {
        "eu data stewardship directive",
        "2025/41",
        "article 14",
        "article 9",
        "article 10",
        "article 11",
        "data steward",
        "restricted",
        "data protection officer",
        "controller",
        "processor",
    },
}

COMBOS: list[tuple[str, bool]] = [
    ("gemma4:e2b-it-qat", True),
    ("gemma4:e2b-it-qat", False),
    ("granite4.1:3b", False),
    ("qwen3.5:2b", False),
    ("nemotron-3-nano:4b", True),
]

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


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _entity_recall(ents: list[dict], gt: set[str]) -> tuple[float, set[str]]:
    if not gt:
        return (1.0, set())
    seen_names = {_norm(e.get("name", "")) for e in ents}
    hits: set[str] = set()
    for target in gt:
        if any(target in n or n in target for n in seen_names if n):
            hits.add(target)
    return (len(hits) / len(gt), hits)


async def call_once(client: AsyncClient, model: str, thinking: bool, text: str) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        resp = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": EXTRACT_PROMPT_SYSTEM},
                {"role": "user", "content": text},
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
            return {"parse_ok": False, "latency_s": latency, "error": str(e)}
        ents = data.get("entities") or []
        rels = data.get("relations") or []
        return {
            "parse_ok": True,
            "latency_s": latency,
            "n_entities": len(ents),
            "n_relations": len(rels),
            "entities": ents,
            "relations": rels,
        }
    except Exception as e:
        return {"parse_ok": False, "latency_s": time.monotonic() - t0, "error": str(e)}


def _agg_stats(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"mean": 0.0, "stdev": 0.0, "n": 0}
    if len(vals) == 1:
        return {"mean": vals[0], "stdev": 0.0, "n": 1}
    return {
        "mean": round(statistics.mean(vals), 3),
        "stdev": round(statistics.stdev(vals), 3),
        "n": len(vals),
    }


async def main() -> None:
    client = AsyncClient(host=OLLAMA_URL)
    docs: dict[str, tuple[str, set[str]]] = {}
    for fname, gt in GROUND_TRUTH.items():
        text_path = CORPUS_DIR / fname
        if not text_path.exists():
            raise RuntimeError(f"missing corpus doc: {text_path}")
        docs[fname] = (text_path.read_text(encoding="utf-8"), gt)
    print(f"Loaded {len(docs)} docs; {len(COMBOS)} combos x {RUNS_PER_COMBO} runs each")
    print(f"Total calls: {len(docs) * len(COMBOS) * RUNS_PER_COMBO}\n")

    by_combo: dict[str, dict[str, Any]] = {}
    for model, thinking in COMBOS:
        combo_key = f"{model} think={thinking}"
        by_combo[combo_key] = {
            "model": model,
            "thinking": thinking,
            "recalls": [],
            "rel_counts": [],
            "ent_counts": [],
            "latencies": [],
            "parse_ok_count": 0,
            "total_calls": 0,
            "per_doc": {},
        }
        print(f"== {combo_key} ==")
        for fname, (text, gt) in docs.items():
            per_doc = {"recalls": [], "rel_counts": [], "ent_counts": [], "latencies": [], "parse_ok": 0}
            for run_idx in range(RUNS_PER_COMBO):
                result = await call_once(client, model, thinking, text)
                by_combo[combo_key]["total_calls"] += 1
                if result.get("parse_ok"):
                    by_combo[combo_key]["parse_ok_count"] += 1
                    per_doc["parse_ok"] += 1
                    recall, hits = _entity_recall(result["entities"], gt)
                    by_combo[combo_key]["recalls"].append(recall)
                    by_combo[combo_key]["rel_counts"].append(result["n_relations"])
                    by_combo[combo_key]["ent_counts"].append(result["n_entities"])
                    by_combo[combo_key]["latencies"].append(result["latency_s"])
                    per_doc["recalls"].append(recall)
                    per_doc["rel_counts"].append(result["n_relations"])
                    per_doc["ent_counts"].append(result["n_entities"])
                    per_doc["latencies"].append(result["latency_s"])
                    print(
                        f"   {fname:30s} run{run_idx+1}: recall={recall:.2f} "
                        f"rel={result['n_relations']:>2} ent={result['n_entities']:>2} "
                        f"lat={result['latency_s']:.1f}s"
                    )
                else:
                    by_combo[combo_key]["latencies"].append(result["latency_s"])
                    err = result.get("error", "?")[:80]
                    print(
                        f"   {fname:30s} run{run_idx+1}: PARSE_FAIL "
                        f"lat={result['latency_s']:.1f}s err={err}"
                    )
            by_combo[combo_key]["per_doc"][fname] = per_doc
        print()

    print("=== aggregate (mean +/- stdev across all parse-ok calls) ===")
    summary: list[dict[str, Any]] = []
    for combo_key, data in by_combo.items():
        recall_s = _agg_stats(data["recalls"])
        rel_s = _agg_stats(data["rel_counts"])
        ent_s = _agg_stats(data["ent_counts"])
        lat_s = _agg_stats(data["latencies"])
        parse_ok_rate = (
            data["parse_ok_count"] / data["total_calls"] if data["total_calls"] else 0
        )
        summary.append(
            {
                "combo": combo_key,
                "parse_ok_rate": round(parse_ok_rate, 3),
                "recall_mean": recall_s["mean"],
                "recall_stdev": recall_s["stdev"],
                "n_relations_mean": rel_s["mean"],
                "n_relations_stdev": rel_s["stdev"],
                "n_entities_mean": ent_s["mean"],
                "n_entities_stdev": ent_s["stdev"],
                "latency_mean_s": lat_s["mean"],
                "latency_stdev_s": lat_s["stdev"],
                "total_calls": data["total_calls"],
            }
        )
        print(
            f"  {combo_key:38s} "
            f"parse_ok={parse_ok_rate:.2f} "
            f"recall={recall_s['mean']:.2f}±{recall_s['stdev']:.2f} "
            f"rel={rel_s['mean']:.1f}±{rel_s['stdev']:.1f} "
            f"ent={ent_s['mean']:.1f} "
            f"lat={lat_s['mean']:.1f}±{lat_s['stdev']:.1f}s"
        )

    # rank by recall, then by n_relations, then by parse_ok_rate descending,
    # then latency ascending
    ranked = sorted(
        summary,
        key=lambda s: (
            -s["recall_mean"],
            -s["n_relations_mean"],
            -s["parse_ok_rate"],
            s["latency_mean_s"],
        ),
    )
    print("\n=== ranking ===")
    for idx, s in enumerate(ranked, start=1):
        print(
            f"  {idx}. {s['combo']:38s} "
            f"recall={s['recall_mean']:.2f}±{s['recall_stdev']:.2f} "
            f"rel={s['n_relations_mean']:.1f}±{s['n_relations_stdev']:.1f} "
            f"parse_ok={s['parse_ok_rate']:.2f} "
            f"lat={s['latency_mean_s']:.1f}s"
        )

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"extract_audit_{stamp}.json"
    out_path.write_text(
        json.dumps(
            {"summary": summary, "ranked": ranked, "by_combo": by_combo, "ground_truth": {k: sorted(v) for k, v in GROUND_TRUTH.items()}},
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
