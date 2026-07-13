"""Regression tests for the cloud-only evaluation policy."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_EVALUATION_ENTRYPOINTS = (
    REPO_ROOT / "eval" / "baseline.py",
    REPO_ROOT / "scripts" / "eval.py",
)


@pytest.mark.parametrize("entrypoint", ACTIVE_EVALUATION_ENTRYPOINTS)
def test_active_evaluation_entrypoints_do_not_offer_ollama(entrypoint: Path) -> None:
    """Acceptance and research entrypoints must stay on the cloud model policy."""
    assert "ollama" not in entrypoint.read_text(encoding="utf-8").lower()
