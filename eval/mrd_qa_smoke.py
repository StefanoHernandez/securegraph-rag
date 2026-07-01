"""Validate and score the MRD realistic-corpus QA smoke suite.

The RAGAS baseline stays under ``eval/baseline.py``. This script is cheaper:
it validates the MRD manifest/QA contract and can deterministically score a
saved query result file for required citations and forbidden-source leakage.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from securgraph.corpus.qa import (  # noqa: E402
    CorpusQaPair,
    QaScore,
    load_corpus_qa_pairs,
    score_qa_result,
)

DEFAULT_CORPUS_ROOT = REPO_ROOT / "corpus" / "mrd_v1"
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"


def _load_result_rows(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(f"Cannot read result file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Result file is not valid JSON: {path}") from exc
    if isinstance(raw, dict) and isinstance(raw.get("results"), list):
        raw = raw["results"]
    if not isinstance(raw, list):
        raise SystemExit("Result file must be a list or an object with a results list")

    rows: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise SystemExit("Each result row needs an id")
        rows[item["id"]] = item
    return rows


def _citation_sources(body: dict[str, Any]) -> list[str]:
    citations = body.get("citations", [])
    if not isinstance(citations, list):
        return []
    sources: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        for key in ("source_uri", "label", "document_id"):
            value = citation.get(key)
            if isinstance(value, str) and value:
                sources.append(value)
                break
    return sources


async def _run_live_queries(
    pairs: tuple[CorpusQaPair, ...],
    *,
    query_url: str,
    tenant_id: str,
    bearer_token: str | None,
    retrieval_mode: str,
    top_k: int,
    timeout_seconds: float,
) -> dict[str, dict[str, Any]]:
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    timeout = httpx.Timeout(timeout_seconds)
    rows: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for pair in pairs:
            payload = {
                "tenant_id": tenant_id,
                "user_id": f"user:{pair.identity}",
                "query": pair.question,
                "options": {
                    "retrieval_mode": retrieval_mode,
                    "top_k": top_k,
                    "consensus_threshold": 0.6,
                },
            }
            try:
                response = await client.post(query_url, json=payload)
            except httpx.HTTPError as exc:
                rows[pair.id] = {
                    "id": pair.id,
                    "status_code": None,
                    "error": str(exc),
                    "answer": "",
                    "cited_sources": [],
                }
                continue
            body = response.json() if response.headers.get("content-type", "").startswith(
                "application/json"
            ) else {}
            answer = body.get("answer", "") if isinstance(body, dict) else ""
            rows[pair.id] = {
                "id": pair.id,
                "status_code": response.status_code,
                "trace_id": body.get("trace_id") if isinstance(body, dict) else None,
                "answer": answer if isinstance(answer, str) else "",
                "cited_sources": _citation_sources(body) if isinstance(body, dict) else [],
            }
    return rows


def _score_pairs(
    pairs: tuple[CorpusQaPair, ...],
    result_rows: dict[str, dict[str, Any]],
) -> tuple[QaScore, ...]:
    scores: list[QaScore] = []
    for pair in pairs:
        row = result_rows.get(pair.id, {})
        answer = row.get("answer", "")
        cited_sources = row.get("cited_sources", [])
        if not isinstance(answer, str):
            answer = ""
        if not isinstance(cited_sources, list) or not all(
            isinstance(source, str) for source in cited_sources
        ):
            cited_sources = []
        scores.append(score_qa_result(pair, answer=answer, cited_sources=cited_sources))
    return tuple(scores)


def _write_report(
    pairs: tuple[CorpusQaPair, ...],
    scores: tuple[QaScore, ...] | None,
    result_rows: dict[str, dict[str, Any]] | None,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"mrd-qa-smoke-{stamp}.json"
    md_path = output_dir / f"mrd-qa-smoke-{stamp}.md"
    allow_count = sum(1 for pair in pairs if pair.outcome == "allow")
    deny_count = sum(1 for pair in pairs if pair.outcome == "deny")
    payload: dict[str, Any] = {
        "corpus": "mrd_v1",
        "pair_count": len(pairs),
        "allow_count": allow_count,
        "deny_count": deny_count,
        "pairs": [asdict(pair) for pair in pairs],
    }
    if scores is not None:
        passed = sum(1 for score in scores if score.passed)
        payload["score"] = {
            "passed": passed,
            "failed": len(scores) - passed,
            "leakage_failures": sum(1 for score in scores if score.forbidden_source_leaked),
            "rows": [asdict(score) for score in scores],
        }
    if result_rows is not None:
        payload["results"] = [result_rows[pair.id] for pair in pairs if pair.id in result_rows]

    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# MRD QA smoke report",
        "",
        f"- Pairs: {len(pairs)}",
        f"- Allow: {allow_count}",
        f"- Deny: {deny_count}",
    ]
    if scores is not None:
        passed = sum(1 for score in scores if score.passed)
        lines.extend(
            [
                f"- Passed: {passed}",
                f"- Failed: {len(scores) - passed}",
                f"- Leakage failures: {sum(1 for s in scores if s.forbidden_source_leaked)}",
                "",
                "| id | outcome | passed | expected | sources | forbidden leak |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for score in scores:
            lines.append(
                "| "
                f"{score.pair_id} | {score.outcome} | {score.passed} | "
                f"{score.expected_text_present} | {score.required_sources_present} | "
                f"{score.forbidden_source_leaked} |"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(prog="mrd-qa-smoke")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    parser.add_argument("--results-json", type=Path)
    parser.add_argument("--live-query-url")
    parser.add_argument("--tenant-id", default="mrd-lab")
    parser.add_argument("--bearer-token")
    parser.add_argument("--retrieval-mode", default="hybrid")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    args = parser.parse_args()
    if args.results_json is not None and args.live_query_url is not None:
        raise SystemExit("--results-json and --live-query-url are mutually exclusive")

    pairs = load_corpus_qa_pairs(args.corpus)
    scores = None
    result_rows = None
    if args.results_json is not None:
        result_rows = _load_result_rows(args.results_json)
    elif args.live_query_url is not None:
        result_rows = asyncio.run(
            _run_live_queries(
                pairs,
                query_url=args.live_query_url,
                tenant_id=args.tenant_id,
                bearer_token=args.bearer_token,
                retrieval_mode=args.retrieval_mode,
                top_k=args.top_k,
                timeout_seconds=args.timeout_seconds,
            )
        )
    if result_rows is not None:
        scores = _score_pairs(pairs, result_rows)
    json_path, md_path = _write_report(pairs, scores, result_rows, args.output_dir)
    print(f"Loaded {len(pairs)} MRD QA pairs from {args.corpus}")
    print(f"Wrote: {json_path}")
    print(f"Wrote: {md_path}")
    if scores is not None and any(not score.passed for score in scores):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
