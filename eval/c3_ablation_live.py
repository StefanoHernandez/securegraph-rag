"""Run the C3 live ablation measurement against a seeded MRD stack.

The runner recomputes communities three times:

1. C3 enabled.
2. C3 partitioning disabled with ``SECURGRAPH_C3_ABLATION_DISABLE_PARTITION=true``.
3. C3 enabled again to restore the stack to the safe state.

For each state it records structural community metrics from Neo4j and probes a
global-mode query as an unauthorized MRD persona.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from securgraph.adapters.community_generator import generate_hierarchical_communities  # noqa: E402
from securgraph.adapters.neo4j import close_neo4j_driver, get_neo4j_driver  # noqa: E402
from securgraph.api.dependencies import bootstrap_openfga, get_openfga_client  # noqa: E402
from securgraph.config import get_settings  # noqa: E402

DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"
ABLATION_FLAG = "SECURGRAPH_C3_ABLATION_DISABLE_PARTITION"
DEFAULT_LEAK_TERMS = (
    "ingestion originals are encrypted at rest",
    "authorization precedes retrieval",
    "audit chain roots require protected storage",
    "only aurora senior technicians",
    "may change this design",
)


@dataclass(frozen=True)
class CommunityRecord:
    community_id: str
    policy_class_key: str
    source_policy_class_keys: tuple[str, ...]
    level: int | None
    title: str


@dataclass(frozen=True)
class StructuralSummary:
    classes: int
    communities: int
    mixed_communities: int
    mixed_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProbeResult:
    status_code: int | None
    trace_id: str | None
    answer: str
    leak_detected: bool
    leak_terms_found: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class AblationRunResult:
    label: str
    partition_enabled: bool
    recompute: dict[str, Any]
    structural: StructuralSummary
    probe: ProbeResult


def _source_keys(value: object, fallback: str) -> tuple[str, ...]:
    if isinstance(value, list):
        keys = sorted({str(item) for item in value if item})
        return tuple(keys or [fallback])
    if isinstance(value, tuple):
        keys = sorted({str(item) for item in value if item})
        return tuple(keys or [fallback])
    return (fallback,)


def _structural_summary(records: list[CommunityRecord]) -> StructuralSummary:
    mixed_ids = tuple(
        record.community_id for record in records if len(record.source_policy_class_keys) > 1
    )
    return StructuralSummary(
        classes=len({record.policy_class_key for record in records}),
        communities=len(records),
        mixed_communities=len(mixed_ids),
        mixed_ids=mixed_ids,
    )


def _leak_terms_found(answer: str, leak_terms: tuple[str, ...]) -> tuple[str, ...]:
    answer_lower = answer.lower()
    return tuple(term for term in leak_terms if term.lower() in answer_lower)


def _detect_leakage(answer: str, leak_terms: tuple[str, ...]) -> bool:
    return bool(_leak_terms_found(answer, leak_terms))


def _fga_user(user_id: str) -> str:
    return user_id if ":" in user_id else f"user:{user_id}"


async def _fetch_community_records(tenant_id: str) -> list[CommunityRecord]:
    driver = get_neo4j_driver()
    cypher = (
        "MATCH (c:Community {tenant_id: $tenant_id}) "
        "RETURN c.community_id AS community_id, "
        "c.policy_class_key AS policy_class_key, "
        "c.source_policy_class_keys AS source_policy_class_keys, "
        "c.level AS level, c.title AS title "
        "ORDER BY c.community_id"
    )
    async with driver.session() as session:
        result = await session.run(cypher, tenant_id=tenant_id)
        rows = await result.data()
    records: list[CommunityRecord] = []
    for row in rows:
        policy_class_key = str(row.get("policy_class_key") or "unlabeled")
        records.append(
            CommunityRecord(
                community_id=str(row["community_id"]),
                policy_class_key=policy_class_key,
                source_policy_class_keys=_source_keys(
                    row.get("source_policy_class_keys"), policy_class_key
                ),
                level=row.get("level"),
                title=str(row.get("title") or ""),
            )
        )
    return records


async def _recompute_communities(tenant_id: str, *, partition_enabled: bool) -> dict[str, Any]:
    old_value = os.environ.get(ABLATION_FLAG)
    if partition_enabled:
        os.environ.pop(ABLATION_FLAG, None)
    else:
        os.environ[ABLATION_FLAG] = "true"
    try:
        settings = get_settings()
        await bootstrap_openfga(settings)
        fga = await get_openfga_client()
        result = await generate_hierarchical_communities(
            get_neo4j_driver(),
            tenant_id=tenant_id,
            fga=fga,
        )
        return dict(result)
    finally:
        if old_value is None:
            os.environ.pop(ABLATION_FLAG, None)
        else:
            os.environ[ABLATION_FLAG] = old_value


async def _run_global_probe(
    *,
    query_url: str,
    tenant_id: str,
    user_id: str,
    question: str,
    bearer_token: str | None,
    top_k: int,
    timeout_seconds: float,
    leak_terms: tuple[str, ...],
) -> ProbeResult:
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    payload = {
        "tenant_id": tenant_id,
        "user_id": _fga_user(user_id),
        "query": question,
        "options": {
            "query_type": "global",
            "retrieval_mode": "hybrid",
            "top_k": top_k,
            "consensus_threshold": 0.6,
        },
    }
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
        ) as client:
            response = await client.post(query_url, json=payload)
    except httpx.HTTPError as exc:
        return ProbeResult(
            status_code=None,
            trace_id=None,
            answer="",
            leak_detected=False,
            leak_terms_found=(),
            error=str(exc),
        )
    body = response.json() if response.headers.get("content-type", "").startswith(
        "application/json"
    ) else {}
    answer = body.get("answer", "") if isinstance(body, dict) else ""
    if not isinstance(answer, str):
        answer = ""
    found = _leak_terms_found(answer, leak_terms)
    return ProbeResult(
        status_code=response.status_code,
        trace_id=body.get("trace_id") if isinstance(body, dict) else None,
        answer=answer,
        leak_detected=bool(found),
        leak_terms_found=found,
        error=None if response.status_code < 400 else str(body),
    )


async def _measure_run(
    *,
    label: str,
    partition_enabled: bool,
    tenant_id: str,
    query_url: str,
    user_id: str,
    question: str,
    bearer_token: str | None,
    top_k: int,
    timeout_seconds: float,
    leak_terms: tuple[str, ...],
) -> AblationRunResult:
    recompute = await _recompute_communities(tenant_id, partition_enabled=partition_enabled)
    records = await _fetch_community_records(tenant_id)
    probe = await _run_global_probe(
        query_url=query_url,
        tenant_id=tenant_id,
        user_id=user_id,
        question=question,
        bearer_token=bearer_token,
        top_k=top_k,
        timeout_seconds=timeout_seconds,
        leak_terms=leak_terms,
    )
    return AblationRunResult(
        label=label,
        partition_enabled=partition_enabled,
        recompute=recompute,
        structural=_structural_summary(records),
        probe=probe,
    )


def _write_report(
    *,
    tenant_id: str,
    query_url: str,
    probe_user: str,
    probe_query: str,
    leak_terms: tuple[str, ...],
    runs: tuple[AblationRunResult, AblationRunResult, AblationRunResult],
    output_dir: Path,
) -> tuple[Path, Path, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"c3-ablation-live-{stamp}.json"
    md_path = output_dir / f"c3-ablation-live-{stamp}.md"
    c3_on, ablation_off, restored = runs
    delta = {
        "classes_on": c3_on.structural.classes,
        "classes_off": ablation_off.structural.classes,
        "classes_restored": restored.structural.classes,
        "communities_on": c3_on.structural.communities,
        "communities_off": ablation_off.structural.communities,
        "communities_restored": restored.structural.communities,
        "mixed_communities_on": c3_on.structural.mixed_communities,
        "mixed_communities_off": ablation_off.structural.mixed_communities,
        "mixed_communities_restored": restored.structural.mixed_communities,
        "leakage_on": c3_on.probe.leak_detected,
        "leakage_off": ablation_off.probe.leak_detected,
        "leakage_restored": restored.probe.leak_detected,
    }
    payload: dict[str, Any] = {
        "kind": "c3_ablation_live",
        "created_at": stamp,
        "tenant_id": tenant_id,
        "query_url": query_url,
        "probe": {
            "user_id": probe_user,
            "question": probe_query,
            "leak_terms": leak_terms,
        },
        "delta": delta,
        "runs": [asdict(run) for run in runs],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# C3 live ablation report",
        "",
        f"- Tenant: `{tenant_id}`",
        f"- Probe user: `{probe_user}`",
        f"- Probe query: {probe_query}",
        "",
        "| run | partition | classes | communities | mixed communities | leak detected | status |",
        "| --- | --- | ---: | ---: | ---: | --- | ---: |",
    ]
    for run in runs:
        lines.append(
            "| "
            f"{run.label} | {run.partition_enabled} | "
            f"{run.structural.classes} | {run.structural.communities} | "
            f"{run.structural.mixed_communities} | {run.probe.leak_detected} | "
            f"{run.probe.status_code} |"
        )
    lines.extend(
        [
            "",
            "## Delta",
            "",
            f"- Mixed communities ON: {delta['mixed_communities_on']}",
            f"- Mixed communities OFF: {delta['mixed_communities_off']}",
            f"- Leakage ON: {delta['leakage_on']}",
            f"- Leakage OFF: {delta['leakage_off']}",
            f"- Leakage restored: {delta['leakage_restored']}",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path, payload


async def _run(args: argparse.Namespace) -> tuple[Path, Path, dict[str, Any]]:
    leak_terms = tuple(args.leak_term or DEFAULT_LEAK_TERMS)
    runs = (
        await _measure_run(
            label="c3_on",
            partition_enabled=True,
            tenant_id=args.tenant_id,
            query_url=args.query_url,
            user_id=args.probe_user,
            question=args.probe_query,
            bearer_token=args.bearer_token,
            top_k=args.top_k,
            timeout_seconds=args.timeout_seconds,
            leak_terms=leak_terms,
        ),
        await _measure_run(
            label="ablation_off",
            partition_enabled=False,
            tenant_id=args.tenant_id,
            query_url=args.query_url,
            user_id=args.probe_user,
            question=args.probe_query,
            bearer_token=args.bearer_token,
            top_k=args.top_k,
            timeout_seconds=args.timeout_seconds,
            leak_terms=leak_terms,
        ),
        await _measure_run(
            label="c3_restored",
            partition_enabled=True,
            tenant_id=args.tenant_id,
            query_url=args.query_url,
            user_id=args.probe_user,
            question=args.probe_query,
            bearer_token=args.bearer_token,
            top_k=args.top_k,
            timeout_seconds=args.timeout_seconds,
            leak_terms=leak_terms,
        ),
    )
    return _write_report(
        tenant_id=args.tenant_id,
        query_url=args.query_url,
        probe_user=args.probe_user,
        probe_query=args.probe_query,
        leak_terms=leak_terms,
        runs=runs,
        output_dir=args.output_dir,
    )


def _strict_failures(payload: dict[str, Any]) -> list[str]:
    delta = payload["delta"]
    failures: list[str] = []
    if delta["mixed_communities_on"] != 0:
        failures.append("C3 ON produced mixed communities")
    if delta["mixed_communities_restored"] != 0:
        failures.append("C3 restored produced mixed communities")
    if delta["mixed_communities_off"] <= 0:
        failures.append("Ablation OFF produced no mixed communities")
    if delta["leakage_on"]:
        failures.append("C3 ON leaked probe terms")
    if delta["leakage_restored"]:
        failures.append("C3 restored leaked probe terms")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(prog="c3-ablation-live")
    parser.add_argument("--tenant-id", default="mrd-lab")
    parser.add_argument("--query-url", default="http://localhost:8000/api/v1/query")
    parser.add_argument("--bearer-token")
    parser.add_argument("--probe-user", default="mrd.junior-tech")
    parser.add_argument(
        "--probe-query",
        default="What controls does the private architecture require?",
    )
    parser.add_argument("--leak-term", action="append")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    try:
        json_path, md_path, payload = asyncio.run(_run(args))
    finally:
        with suppress(Exception):
            asyncio.run(close_neo4j_driver())
    print(f"Wrote: {json_path}")
    print(f"Wrote: {md_path}")
    if args.strict:
        failures = _strict_failures(payload)
        if failures:
            raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
