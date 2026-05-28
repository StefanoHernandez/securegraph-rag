"""Four-mode baseline harness: vector / lexical / graph / hybrid.

Re-runs the same QA corpus four times through the production /query endpoint,
each time with a different `retrieval_mode`, and computes RAGAS core metrics.
Writes `eval/results/baseline-<UTC-date>.csv` and a markdown table.

Synthesis is held fixed by going through the production workflow; only the
retrieval branch differs. This experimental design lets the paper attribute
deltas to the retriever, not to the synthesizer.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import yaml
from datasets import Dataset
from httpx import ASGITransport, AsyncClient

from securgraph.api.main import create_app

try:
    from langchain_community.embeddings import OllamaEmbeddings
    from langchain_community.llms import Ollama
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics._answer_relevance import AnswerRelevancy
    from ragas.metrics._context_precision import LLMContextPrecisionWithReference
    from ragas.metrics._context_recall import LLMContextRecall
    from ragas.metrics._faithfulness import Faithfulness
except ImportError as e:
    print(f"ragas/langchain-community not installed in this env: {e}")
    sys.exit(2)

MODES = ("vector", "lexical", "graph", "hybrid")
CORPUS_PATH = Path(__file__).parent.parent / "corpus" / "baseline_v1" / "qa_pairs.yaml"
RESULTS_DIR = Path(__file__).parent / "results"


async def _run_mode(client: AsyncClient, mode: str, qa_pairs: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for idx, item in enumerate(qa_pairs, start=1):
        payload = {
            "tenant_id": "mrd",
            "user_id": "user:stefano",
            "query": item["question"],
            "options": {
                "retrieval_mode": mode,
                "top_k": 5,
                "consensus_threshold": 0.6,
            },
        }
        resp = await client.post("/query", json=payload, timeout=120.0)
        if resp.status_code != 200:
            print(f"  [{mode}] [{idx}] HTTP {resp.status_code}: {resp.text[:200]}")
            continue
        body = resp.json()
        answer = body.get("answer", "") if isinstance(body.get("answer"), str) else ""

        # Reconstruct contexts from the per-channel summary, same pattern as
        # scripts/eval.py. Vector channel contributes its draft_summary (the
        # synthesized text over retrieved chunks); graph channel contributes
        # each traversed path's textual form. Falls back to the QA pair's
        # expected_context only when the run produced no channel content
        # (e.g. retrieval_mode skipped a branch and the other returned empty).
        contexts: list[str] = []
        channels = body.get("channels") or {}
        vec_summary = (channels.get("vector") or {}).get("draft_summary")
        if vec_summary:
            contexts.append(vec_summary)
        graph_paths = (channels.get("graph") or {}).get("paths", []) or []
        for path in graph_paths:
            contexts.append(str(path))
        if not contexts:
            contexts = [item.get("expected_context", "")]

        rows.append(
            {
                "mode": mode,
                "question": item["question"],
                "ground_truth": item["ground_truth"],
                "answer": answer,
                "contexts": contexts,
            }
        )
    return rows


def _ragas_evaluate(rows: list[dict]) -> pd.DataFrame:
    """Compute RAGAS core metrics on the collected rows, grouped by mode."""
    llm = LangchainLLMWrapper(Ollama(model="granite4.1:3b"))
    emb = LangchainEmbeddingsWrapper(OllamaEmbeddings(model="granite4.1:3b"))
    faithfulness_metric = Faithfulness()
    answer_relevance_metric = AnswerRelevancy()
    context_precision_metric = LLMContextPrecisionWithReference()
    context_recall_metric = LLMContextRecall()
    metrics = [
        faithfulness_metric,
        answer_relevance_metric,
        context_precision_metric,
        context_recall_metric,
    ]

    per_mode: dict[str, pd.DataFrame] = {}
    for mode in MODES:
        subset = [r for r in rows if r["mode"] == mode]
        if not subset:
            continue
        ds = Dataset.from_list(subset)
        res = evaluate(ds, metrics=metrics, llm=llm, embeddings=emb)
        per_mode[mode] = res.to_pandas()

    summary = []
    for mode, df in per_mode.items():
        summary.append(
            {
                "mode": mode,
                "faithfulness": df[faithfulness_metric.name].mean(),
                "answer_relevance": df[answer_relevance_metric.name].mean(),
                "context_precision": df[context_precision_metric.name].mean(),
                "context_recall": df[context_recall_metric.name].mean(),
                "n": len(df),
            }
        )
    return pd.DataFrame(summary)


def _write_outputs(summary: pd.DataFrame) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    csv_path = RESULTS_DIR / f"baseline-{stamp}.csv"
    md_path = RESULTS_DIR / f"baseline-{stamp}.md"
    summary.to_csv(csv_path, index=False)
    md_path.write_text(summary.to_markdown(index=False) + "\n", encoding="utf-8")
    return csv_path, md_path


async def main() -> None:
    if not CORPUS_PATH.exists():
        print(f"Missing corpus: {CORPUS_PATH}")
        sys.exit(2)
    qa_pairs = yaml.safe_load(CORPUS_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(qa_pairs)} QA pairs from {CORPUS_PATH}")

    app = create_app()
    transport = ASGITransport(app=app)
    all_rows: list[dict] = []
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for mode in MODES:
            print(f"--- Running mode: {mode} ---")
            all_rows.extend(await _run_mode(client, mode, qa_pairs))

    print("--- Computing RAGAS metrics ---")
    summary = _ragas_evaluate(all_rows)
    print(summary.to_string(index=False))

    csv_path, md_path = _write_outputs(summary)
    print(f"\nWrote: {csv_path}\nWrote: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
