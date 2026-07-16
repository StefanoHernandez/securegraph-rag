"""Validate and score the MRD realistic-corpus QA smoke suite.

The RAGAS baseline stays under ``eval/baseline.py``. This script is cheaper:
it validates the MRD manifest/QA contract and can deterministically score a
saved query result file for required citations and forbidden-source leakage.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from securgraph.adapters.llm import get_llm_adapter_for  # noqa: E402
from securgraph.corpus.manifest import load_corpus_manifest  # noqa: E402
from securgraph.corpus.qa import (  # noqa: E402
    CorpusQaPair,
    QaScore,
    load_corpus_qa_pairs,
    score_qa_result,
)
from securgraph.runtime_overrides import get_effective_settings  # noqa: E402

DEFAULT_CORPUS_ROOT = REPO_ROOT / "corpus" / "mrd_v1"
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"

_SUBMISSION_MODELS = {
    "generation": {"provider": "mistral", "model": "mistral-small-latest"},
    "extraction": {"provider": "mistral", "model": "mistral-small-latest"},
    "judge": {"provider": "openai", "model": "gpt-4o-mini"},
}


class SemanticJudge(Protocol):
    async def judge_nli(self, claim: str, reference: str) -> Any: ...


def _query_service_models_from_config(config: object) -> dict[str, dict[str, str]]:
    """Normalize the non-secret config snapshot exposed by the query service."""
    if not isinstance(config, dict):
        raise SystemExit("query service runtime configuration is not a JSON object")
    fields = {"generation": "llm", "extraction": "extract", "judge": "judge"}
    models: dict[str, dict[str, str]] = {}
    for role, field in fields.items():
        raw = config.get(field)
        if not isinstance(raw, dict):
            raise SystemExit(f"query service runtime configuration is missing {field}")
        provider = raw.get("provider")
        model = raw.get("model")
        if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
            raise SystemExit(f"query service runtime configuration has invalid {field} metadata")
        models[role] = {"provider": provider, "model": model}
    return models


def _require_matching_models(
    expected: dict[str, dict[str, str]],
    observed: dict[str, dict[str, str]],
    *,
    source: str,
) -> None:
    """Fail a submission run if an observed runtime differs from its manifest."""
    for role, expected_model in expected.items():
        if observed.get(role) != expected_model:
            raise SystemExit(
                f"{source} runtime configuration for {role} does not match "
                f"the submission model requirement"
            )


def _semantic_judge_model(tenant_id: str) -> dict[str, str]:
    """Resolve the evaluator's own judge, including the tenant override path."""
    settings = get_effective_settings(tenant_id)
    return {
        "provider": settings.llm_provider_judge or settings.llm_provider,
        "model": settings.llm_model_judge or settings.llm_model,
    }


def _query_service_config_url(query_url: str, tenant_id: str) -> str:
    parsed = urlsplit(query_url)
    valid_query_url = (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and parsed.path.endswith("/query")
    )
    if not valid_query_url:
        raise SystemExit("--live-query-url must be an absolute URL ending in /query")
    config_path = f"{parsed.path.rsplit('/', 1)[0]}/config"
    return urlunsplit(
        (parsed.scheme, parsed.netloc, config_path, urlencode({"tenant_id": tenant_id}), "")
    )


def _fetch_query_service_models(
    *,
    query_url: str,
    tenant_id: str,
    bearer_token: str | None,
    timeout_seconds: float,
) -> dict[str, dict[str, str]]:
    """Fetch the exact provider/model tuple resolved by the live query service."""
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    try:
        response = httpx.get(
            _query_service_config_url(query_url, tenant_id),
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise SystemExit(f"Could not fetch query service runtime configuration: {exc}") from exc
    try:
        return _query_service_models_from_config(response.json())
    except ValueError as exc:
        raise SystemExit("query service runtime configuration is not valid JSON") from exc


def _hash_corpus(root: Path) -> str:
    """Return a stable SHA-256 over corpus paths and file contents."""
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_value(*args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable is required to build an evaluation manifest")
    completed = subprocess.run(  # noqa: S603 - executable resolved from trusted PATH
        [git, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _build_run_manifest(
    *,
    corpus_root: Path,
    generation_provider: str,
    generation_model: str,
    extraction_provider: str,
    extraction_model: str,
    judge_provider: str,
    judge_model: str,
    prompt_version: str,
    seed: int,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Capture the inputs needed to reproduce an evaluation run."""
    return {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_value("rev-parse", "HEAD"),
        "git_dirty": bool(_git_value("status", "--porcelain")),
        "corpus_sha256": _hash_corpus(corpus_root),
        "models": {
            "generation": {"provider": generation_provider, "model": generation_model},
            "extraction": {"provider": extraction_provider, "model": extraction_model},
            "judge": {"provider": judge_provider, "model": judge_model},
        },
        "prompt_version": prompt_version,
        "seed": seed,
        "configuration": configuration,
    }


def _validate_submission_manifest(manifest: dict[str, Any], corpus_root: Path) -> None:
    """Reject configurations that cannot support the frozen submission protocol."""
    models = manifest.get("models")
    if not isinstance(models, dict):
        raise SystemExit("Evaluation manifest is missing model metadata")
    for role, expected in _SUBMISSION_MODELS.items():
        if models.get(role) != expected:
            raise SystemExit(
                f"Submission evaluation requires {role}={expected['provider']}/"
                f"{expected['model']}"
            )

    if corpus_root.name != "mrd_heldout_v1":
        return
    if manifest.get("git_dirty"):
        raise SystemExit("Held-out evaluation requires a clean git worktree")
    configuration = manifest.get("configuration")
    if not isinstance(configuration, dict) or configuration.get("baseline_templates"):
        raise SystemExit("Held-out evaluation must run with baseline templates disabled")
    if not configuration.get("semantic_judge"):
        raise SystemExit("Held-out evaluation requires the configured OpenAI semantic judge")


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
        if item["id"] in rows:
            raise SystemExit(f"Result file contains a duplicate id: {item['id']}")
        rows[item["id"]] = item
    return rows


def _validate_result_rows(
    pairs: tuple[CorpusQaPair, ...], result_rows: dict[str, dict[str, Any]]
) -> None:
    """Require exactly one result for every frozen corpus case before scoring."""
    expected_ids = {pair.id for pair in pairs}
    received_ids = set(result_rows)
    missing_ids = sorted(expected_ids - received_ids)
    unexpected_ids = sorted(received_ids - expected_ids)
    if missing_ids:
        raise SystemExit(f"Result file is missing results for: {', '.join(missing_ids)}")
    if unexpected_ids:
        raise SystemExit(f"Result file contains unexpected result ids: {', '.join(unexpected_ids)}")


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


def _confidence_diagnostics(body: dict[str, Any]) -> dict[str, Any]:
    """Keep C2 diagnostics needed for calibration, never drafts or evidence text."""
    diagnostics: dict[str, Any] = {}
    confidence = body.get("confidence")
    if isinstance(confidence, dict):
        safe_confidence: dict[str, Any] = {}
        for field in ("agreement_score", "entity_overlap", "claim_nli_score", "evidence_coverage"):
            value = confidence.get(field)
            if value is None or (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and 0.0 <= float(value) <= 1.0
            ):
                safe_confidence[field] = value
        if isinstance(confidence.get("high_trust"), bool):
            safe_confidence["high_trust"] = confidence["high_trust"]
        if safe_confidence:
            diagnostics["confidence"] = safe_confidence

    explanation = body.get("explanation")
    if isinstance(explanation, dict):
        retrieval_mode = explanation.get("retrieval_mode")
        consensus_verdict = explanation.get("consensus_verdict")
        if isinstance(retrieval_mode, str):
            diagnostics["retrieval_mode"] = retrieval_mode
        if isinstance(consensus_verdict, str) or consensus_verdict is None:
            diagnostics["consensus_verdict"] = consensus_verdict
    return diagnostics


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
                    "include_explanation": True,
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
                **(_confidence_diagnostics(body) if isinstance(body, dict) else {}),
            }
    return rows


async def _annotate_semantic_leakage(
    pairs: tuple[CorpusQaPair, ...],
    result_rows: dict[str, dict[str, Any]],
    judge: SemanticJudge,
) -> None:
    """Record secondary NLI leakage evidence without changing deterministic gates."""
    for pair in pairs:
        if pair.case_type != "authorization_denial" or not pair.forbidden_claims:
            continue
        row = result_rows.get(pair.id)
        if row is None:
            continue
        answer = row.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            continue
        judgments: list[dict[str, object]] = []
        leak_scores: list[float] = []
        for claim in pair.forbidden_claims:
            result = await judge.judge_nli(claim, answer)
            judgments.append(
                {
                    "claim": claim,
                    "label": result.label,
                    "confidence": result.confidence,
                    "reason": result.reason,
                }
            )
            leak_scores.append(result.confidence if result.label == "entailment" else 0.0)
        row["semantic_leak_score"] = max(leak_scores, default=0.0)
        row["semantic_judgments"] = judgments


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
        raw_semantic_score = row.get("semantic_leak_score")
        semantic_leak_score = (
            float(raw_semantic_score)
            if isinstance(raw_semantic_score, (int, float))
            and not isinstance(raw_semantic_score, bool)
            and 0.0 <= float(raw_semantic_score) <= 1.0
            else None
        )
        scores.append(
            score_qa_result(
                pair,
                answer=answer,
                cited_sources=cited_sources,
                semantic_leak_score=semantic_leak_score,
            )
        )
    return tuple(scores)


def _report_slug(corpus_root: Path) -> str:
    name = corpus_root.name
    if name == "mrd_heldout_v1":
        return "mrd-heldout"
    if name == "mrd_v1":
        return "mrd-qa-smoke"
    return f"mrd-qa-smoke-{name}"


def _write_report(
    pairs: tuple[CorpusQaPair, ...],
    scores: tuple[QaScore, ...] | None,
    result_rows: dict[str, dict[str, Any]] | None,
    output_dir: Path,
    run_manifest: dict[str, Any],
    *,
    corpus_root: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    slug = _report_slug(corpus_root)
    json_path = output_dir / f"{slug}-{stamp}.json"
    md_path = output_dir / f"{slug}-{stamp}.md"
    allow_count = sum(1 for pair in pairs if pair.outcome == "allow")
    deny_count = sum(1 for pair in pairs if pair.outcome == "deny")
    payload: dict[str, Any] = {
        "corpus": corpus_root.name,
        "pair_count": len(pairs),
        "allow_count": allow_count,
        "deny_count": deny_count,
        "run": run_manifest,
        "pairs": [asdict(pair) for pair in pairs],
    }
    if scores is not None:
        passed = sum(1 for score in scores if score.passed)
        pair_by_id = {pair.id: pair for pair in pairs}
        case_types = {
            case_type: {"total": 0, "passed": 0, "failed": 0}
            for case_type in ("answer_quality", "authorization_denial", "source_isolation")
        }
        for score in scores:
            case_type = pair_by_id[score.pair_id].case_type
            bucket = case_types[case_type]
            bucket["total"] += 1
            bucket["passed" if score.passed else "failed"] += 1
        payload["score"] = {
            "passed": passed,
            "failed": len(scores) - passed,
            "forbidden_source_leaks": sum(
                1 for score in scores if score.forbidden_source_leaked
            ),
            "forbidden_claim_leaks": sum(
                1 for score in scores if score.forbidden_claim_leaked
            ),
            "refusal_failures": sum(
                1
                for score in scores
                if score.outcome == "deny"
                and not score.refusal_detected
                and next(pair for pair in pairs if pair.id == score.pair_id).case_type
                == "authorization_denial"
            ),
            "case_types": case_types,
            "rows": [asdict(score) for score in scores],
        }
    if result_rows is not None:
        payload["results"] = [result_rows[pair.id] for pair in pairs if pair.id in result_rows]

    canonical = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    payload["results_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    title = "MRD held-out QA report" if slug == "mrd-heldout" else "MRD QA smoke report"
    lines = [
        f"# {title}",
        "",
        "> Metric: expected-text matching is *normalized containment* "
        "(lowercase, markdown/punctuation stripped, key-fact token fallback), "
        "not exact substring. Required-source and deny/leakage checks are exact.",
        "",
        f"- Commit: `{run_manifest.get('git_commit', 'unknown')}`",
        f"- Git worktree dirty: `{run_manifest.get('git_dirty', 'unknown')}`",
        f"- Corpus SHA-256: `{run_manifest.get('corpus_sha256', 'unknown')}`",
        f"- Results SHA-256: `{payload['results_sha256']}`",
        f"- Prompt version: `{run_manifest.get('prompt_version', 'unknown')}`",
        f"- Seed: `{run_manifest.get('seed', 'unknown')}`",
        f"- Pairs: {len(pairs)}",
        f"- Allow: {allow_count}",
        f"- Deny: {deny_count}",
    ]
    models = run_manifest.get("models")
    if isinstance(models, dict):
        for label, role in (
            ("Generation", "generation"),
            ("Extraction", "extraction"),
            ("Judge", "judge"),
        ):
            metadata = models.get(role)
            if not isinstance(metadata, dict):
                continue
            provider = metadata.get("provider")
            model = metadata.get("model")
            if isinstance(provider, str) and isinstance(model, str):
                lines.append(f"- {label}: `{provider}/{model}`")
    configuration = run_manifest.get("configuration")
    if isinstance(configuration, dict):
        lines.append(
            "- Configuration: `"
            + json.dumps(configuration, sort_keys=True, separators=(",", ":"))
            + "`"
        )
    if scores is not None:
        passed = sum(1 for score in scores if score.passed)
        lines.extend(
            [
                f"- Passed: {passed}",
                f"- Failed: {len(scores) - passed}",
                f"- Forbidden-source leaks: {sum(1 for s in scores if s.forbidden_source_leaked)}",
                f"- Forbidden-claim leaks: {sum(1 for s in scores if s.forbidden_claim_leaked)}",
                "",
                "| id | outcome | passed | expected | sources | refusal | "
                "source leak | claim leak | semantic |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for score in scores:
            lines.append(
                "| "
                f"{score.pair_id} | {score.outcome} | {score.passed} | "
                f"{score.expected_text_present} | {score.required_sources_present} | "
                f"{score.refusal_detected} | {score.forbidden_source_leaked} | "
                f"{score.forbidden_claim_leaked} | {score.semantic_leak_score} |"
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
    parser.add_argument("--generation-provider", default="mistral")
    parser.add_argument("--generation-model", default="mistral-small-latest")
    parser.add_argument("--extraction-provider", default="mistral")
    parser.add_argument("--extraction-model", default="mistral-small-latest")
    parser.add_argument("--judge-provider", default="openai")
    parser.add_argument("--judge-model", default="gpt-4o-mini")
    parser.add_argument("--prompt-version", default="securegraph-query-v1")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--semantic-judge",
        action="store_true",
        help="Run the configured OpenAI judge as a secondary semantic leakage measure.",
    )
    args = parser.parse_args()
    if args.results_json is not None and args.live_query_url is not None:
        raise SystemExit("--results-json and --live-query-url are mutually exclusive")

    corpus_manifest = load_corpus_manifest(args.corpus)
    if args.tenant_id != corpus_manifest.tenant_id:
        raise SystemExit(
            "--tenant-id must match the tenant_id declared by the evaluation corpus"
        )
    pairs = load_corpus_qa_pairs(args.corpus)
    run_manifest = _build_run_manifest(
        corpus_root=args.corpus,
        generation_provider=args.generation_provider,
        generation_model=args.generation_model,
        extraction_provider=args.extraction_provider,
        extraction_model=args.extraction_model,
        judge_provider=args.judge_provider,
        judge_model=args.judge_model,
        prompt_version=args.prompt_version,
        seed=args.seed,
        configuration={
            "tenant_id": args.tenant_id,
            "retrieval_mode": args.retrieval_mode,
            "top_k": args.top_k,
            "timeout_seconds": args.timeout_seconds,
            "mode": "live" if args.live_query_url else "saved" if args.results_json else "validate",
            "semantic_judge": args.semantic_judge,
            "baseline_templates": os.environ.get(
                "SECURGRAPH_EVAL_BASELINE_TEMPLATES", ""
            ).strip().lower()
            in {"1", "true", "yes", "on"},
        },
    )
    # Freeze the held-out protocol before contacting its live API endpoint.
    _validate_submission_manifest(run_manifest, args.corpus)
    declared_models = {
        "generation": {
            "provider": args.generation_provider,
            "model": args.generation_model,
        },
        "extraction": {
            "provider": args.extraction_provider,
            "model": args.extraction_model,
        },
        "judge": {
            "provider": args.judge_provider,
            "model": args.judge_model,
        },
    }
    configuration = run_manifest.get("configuration")
    if not isinstance(configuration, dict):
        raise SystemExit("Evaluation manifest is missing configuration metadata")
    if args.live_query_url is not None:
        query_service_models = _fetch_query_service_models(
            query_url=args.live_query_url,
            tenant_id=args.tenant_id,
            bearer_token=args.bearer_token,
            timeout_seconds=args.timeout_seconds,
        )
        _require_matching_models(
            declared_models,
            query_service_models,
            source="query service",
        )
        # The report records the observed query-service snapshot, not merely
        # the runner's command-line defaults.
        run_manifest["models"] = query_service_models
        configuration["query_service_models"] = query_service_models
    if args.semantic_judge:
        semantic_judge_runtime = _semantic_judge_model(args.tenant_id)
        _require_matching_models(
            {"judge": declared_models["judge"]},
            {"judge": semantic_judge_runtime},
            source="semantic judge",
        )
        configuration["semantic_judge_runtime"] = semantic_judge_runtime
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
        _validate_result_rows(pairs, result_rows)
        if args.semantic_judge:
            judge = get_llm_adapter_for("judge", tenant_id=args.tenant_id)
            asyncio.run(_annotate_semantic_leakage(pairs, result_rows, judge))
        scores = _score_pairs(pairs, result_rows)
    json_path, md_path = _write_report(
        pairs,
        scores,
        result_rows,
        args.output_dir,
        run_manifest,
        corpus_root=args.corpus,
    )
    print(f"Loaded {len(pairs)} MRD QA pairs from {args.corpus}")
    print(f"Wrote: {json_path}")
    print(f"Wrote: {md_path}")
    if scores is not None and any(not score.passed for score in scores):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
