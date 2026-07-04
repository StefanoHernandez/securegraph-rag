from __future__ import annotations

import pytest
from eval.c3_ablation_live import (
    CommunityRecord,
    _detect_leakage,
    _strict_failures,
    _structural_summary,
)

pytestmark = pytest.mark.unit


def test_structural_summary_counts_mixed_communities() -> None:
    summary = _structural_summary(
        [
            CommunityRecord(
                community_id="comm-a",
                policy_class_key="class-a",
                source_policy_class_keys=("class-a",),
                level=0,
                title="A",
            ),
            CommunityRecord(
                community_id="comm-mixed",
                policy_class_key="unlabeled",
                source_policy_class_keys=("class-a", "class-b", "class-c"),
                level=0,
                title="Mixed",
            ),
        ]
    )

    assert summary.classes == 2
    assert summary.communities == 2
    assert summary.mixed_communities == 1
    assert summary.mixed_ids == ("comm-mixed",)


def test_detect_leakage_is_case_insensitive() -> None:
    assert _detect_leakage(
        "The answer mentions Private Architecture controls.",
        ("private architecture",),
    )


def test_strict_mode_accepts_structural_delta_without_textual_ablation_leak() -> None:
    payload = {
        "delta": {
            "mixed_communities_on": 0,
            "mixed_communities_off": 12,
            "mixed_communities_restored": 0,
            "leakage_on": False,
            "leakage_off": False,
            "leakage_restored": False,
        }
    }

    assert _strict_failures(payload) == []
    assert not _detect_leakage(
        "The answer refuses to disclose the restricted source.",
        ("private architecture",),
    )
