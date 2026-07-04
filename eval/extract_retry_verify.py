"""Verify the extract_graph_slice retry fix.

The audit (eval/results/extract_audit_20260611T105341Z.json) showed gemma4
think=True returns relations=[] in ~67% of trials on doc 01 even when
entities are extracted correctly. The fix in
src/securgraph/adapters/ollama.py adds a one-shot retry with temperature
0.2 when entities>=3 and relations==[]. This script runs N trials on the
same chunk with the fix active and reports the post-fix rate of
relations>0.

We need >=80% to consider the fix successful.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

from securgraph.adapters.ollama import extract_graph_slice

N_TRIALS = 5
DOC_PATH = (
    Path(__file__).parent.parent / "corpus" / "baseline_v1" / "documents" / "01_internal_policy.txt"
)


async def main() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    print(f"chunk len: {len(text)} chars, N_TRIALS={N_TRIALS}")
    print("baseline (pre-fix audit): 33% rate of relations>0, mean rel=8.0 stdev=8.0\n")

    rel_counts: list[int] = []
    ent_counts: list[int] = []
    latencies: list[float] = []
    has_rel = 0
    for i in range(N_TRIALS):
        t0 = time.monotonic()
        slice_ = await extract_graph_slice(text)
        dt = time.monotonic() - t0
        rel_counts.append(len(slice_.relations))
        ent_counts.append(len(slice_.entities))
        latencies.append(dt)
        if slice_.relations:
            has_rel += 1
        print(
            f"  trial {i+1}: entities={len(slice_.entities):>2} "
            f"relations={len(slice_.relations):>2} "
            f"latency={dt:.1f}s"
        )

    print()
    rate = has_rel / N_TRIALS
    print(f"relations>0 rate: {rate:.0%} ({has_rel}/{N_TRIALS}) [target >=80%]")
    if rel_counts:
        rel_stdev = statistics.stdev(rel_counts) if len(rel_counts) > 1 else 0
        print(f"relation count: mean={statistics.mean(rel_counts):.1f} stdev={rel_stdev:.1f}")
    if ent_counts:
        ent_stdev = statistics.stdev(ent_counts) if len(ent_counts) > 1 else 0
        print(f"entity count:   mean={statistics.mean(ent_counts):.1f} stdev={ent_stdev:.1f}")
    if latencies:
        print(f"latency:        mean={statistics.mean(latencies):.1f}s p_max={max(latencies):.1f}s")
    print()
    if rate >= 0.8:
        print("VERDICT: PASS — fix is effective.")
    elif rate > 0.33:
        print("VERDICT: PARTIAL — improved but below target.")
    else:
        print("VERDICT: NO_IMPROVEMENT — fix not effective.")


if __name__ == "__main__":
    asyncio.run(main())
