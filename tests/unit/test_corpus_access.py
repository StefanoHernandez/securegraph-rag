from __future__ import annotations

import pytest

from securgraph.corpus.access import (
    group_viewer_ids_for_document,
    tenant_authenticated_group_id,
)

pytestmark = pytest.mark.unit


def test_public_document_adds_tenant_authenticated_group() -> None:
    assert tenant_authenticated_group_id("mrd-lab") == "tenant-authenticated-mrd-lab"
    assert group_viewer_ids_for_document(
        tenant_id="mrd-lab",
        classification="public",
        manifest_groups=["project-aurora"],
    ) == ("project-aurora", "tenant-authenticated-mrd-lab")


def test_non_public_document_keeps_manifest_groups_only() -> None:
    assert group_viewer_ids_for_document(
        tenant_id="mrd-lab",
        classification="restricted",
        manifest_groups=["project-aurora", "project-aurora"],
    ) == ("project-aurora",)
