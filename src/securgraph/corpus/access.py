"""Corpus access helpers shared by seed and evaluation scripts."""

from __future__ import annotations

from collections.abc import Iterable

TENANT_AUTHENTICATED_GROUP_PREFIX = "tenant-authenticated"


def tenant_authenticated_group_id(tenant_id: str) -> str:
    """Synthetic FGA group containing every authenticated user in one tenant."""
    return f"{TENANT_AUTHENTICATED_GROUP_PREFIX}-{tenant_id}"


def group_viewer_ids_for_document(
    *,
    tenant_id: str,
    classification: str,
    manifest_groups: Iterable[str],
) -> tuple[str, ...]:
    """Return document group viewers, adding tenant-wide access for public docs."""
    groups: list[str] = []
    seen: set[str] = set()
    for group_id in manifest_groups:
        if group_id not in seen:
            groups.append(group_id)
            seen.add(group_id)
    if classification == "public":
        public_group = tenant_authenticated_group_id(tenant_id)
        if public_group not in seen:
            groups.append(public_group)
    return tuple(groups)
