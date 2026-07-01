"""Strict loader for pseudonymized evaluation corpus manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

VALID_CLASSIFICATIONS = frozenset({"public", "internal", "confidential", "restricted"})


class CorpusManifestError(ValueError):
    """Raised when a corpus manifest cannot safely be seeded."""


@dataclass(frozen=True)
class CorpusDocument:
    path: Path
    owner: str
    classification: str
    groups: tuple[str, ...]
    direct_viewers: tuple[str, ...]


@dataclass(frozen=True)
class CorpusManifest:
    root: Path
    tenant_id: str
    users: frozenset[str]
    groups: frozenset[str]
    documents: tuple[CorpusDocument, ...]


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CorpusManifestError(f"Cannot read {path.name}") from exc
    if not isinstance(value, dict):
        raise CorpusManifestError(f"{path.name} must contain a mapping")
    return value


def _ids(items: object, key: str) -> frozenset[str]:
    if not isinstance(items, list):
        raise CorpusManifestError(f"{key} must be a list")
    values: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise CorpusManifestError(f"Every {key} entry needs a string id")
        values.add(item["id"])
    return frozenset(values)


def load_corpus_manifest(root: Path) -> CorpusManifest:
    """Load a corpus only when all document access references are resolvable."""
    root = root.resolve()
    principals = _read_yaml(root / "principals.yaml")
    users = _ids(principals.get("users"), "users")
    groups = _ids(principals.get("groups"), "groups")
    manifest = _read_yaml(root / "manifest.yaml")
    tenant_id = manifest.get("tenant_id")
    documents = manifest.get("documents")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise CorpusManifestError("manifest tenant_id must be a non-empty string")
    if not isinstance(documents, list):
        raise CorpusManifestError("manifest documents must be a list")

    loaded: list[CorpusDocument] = []
    for item in documents:
        if not isinstance(item, dict):
            raise CorpusManifestError("Each document entry must be a mapping")
        relative_path = item.get("path")
        owner = item.get("owner")
        classification = item.get("classification")
        group_ids = item.get("groups", [])
        direct_viewers = item.get("direct_viewers", [])
        if not isinstance(relative_path, str) or not isinstance(owner, str):
            raise CorpusManifestError("Each document needs path and owner")
        path = (root / relative_path).resolve()
        if root not in path.parents or not path.is_file():
            raise CorpusManifestError(
                f"Document path is missing or escapes corpus root: {relative_path}"
            )
        if owner not in users:
            raise CorpusManifestError(f"Document owner is an unknown user: {owner}")
        if classification not in VALID_CLASSIFICATIONS:
            raise CorpusManifestError(f"Invalid document classification: {classification}")
        if not isinstance(group_ids, list) or not all(
            isinstance(value, str) for value in group_ids
        ):
            raise CorpusManifestError("Document groups must be a list of ids")
        if not isinstance(direct_viewers, list) or not all(
            isinstance(value, str) for value in direct_viewers
        ):
            raise CorpusManifestError("Document direct_viewers must be a list of user ids")
        unknown_groups = set(group_ids) - groups
        if unknown_groups:
            raise CorpusManifestError(
                f"Document references unknown group: {sorted(unknown_groups)[0]}"
            )
        unknown_viewers = set(direct_viewers) - users
        if unknown_viewers:
            raise CorpusManifestError(
                f"Document references unknown direct viewer: {sorted(unknown_viewers)[0]}"
            )
        loaded.append(
            CorpusDocument(
                path=path,
                owner=owner,
                classification=classification,
                groups=tuple(group_ids),
                direct_viewers=tuple(direct_viewers),
            )
        )
    return CorpusManifest(
        root=root,
        tenant_id=tenant_id,
        users=users,
        groups=groups,
        documents=tuple(loaded),
    )
