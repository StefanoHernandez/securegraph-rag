"""Seed the MRD corpus into a live stack for QA scoring.

Creates the tenant, seeds FGA group memberships + document access tuples,
ingests all manifest documents, and optionally recomputes communities.

Run after `make up` + `alembic upgrade head` + API started with:
    SECURGRAPH_EVAL_IDENTITY_MODE=true SECURGRAPH_AUTH_ENABLED=false

Usage:
    .venv/bin/python eval/mrd_seed_live.py [--corpus corpus/mrd_v1] [--recompute-communities]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from securgraph.corpus.access import (  # noqa: E402
    group_viewer_ids_for_document,
    tenant_authenticated_group_id,
)
from securgraph.corpus.manifest import CorpusManifest, load_corpus_manifest  # noqa: E402


def _load_principals(root: Path) -> tuple[list[dict], list[str]]:
    """Load users and groups from principals.yaml."""
    import yaml

    data = yaml.safe_load((root / "principals.yaml").read_text())
    users = data.get("users", [])
    groups = [g["id"] for g in data.get("groups", [])]
    return users, groups


async def _get_fga_client():
    """Bootstrap an OpenFGA client for seeding tuples.

    Auto-discovers the store if not configured in .env (the API does the same
    on startup, but this script runs in a separate process).
    """
    from openfga_sdk import OpenFgaClient
    from openfga_sdk.models import CreateStoreRequest

    from securgraph.adapters.openfga import build_openfga_configuration
    from securgraph.config import get_settings

    settings = get_settings()
    store_id = settings.openfga_store_id or None
    model_id = settings.openfga_authorization_model_id or None

    if not store_id:
        # Auto-discover or create the store, mirroring API startup behavior.
        temp_client = OpenFgaClient(build_openfga_configuration(settings))
        try:
            stores = await temp_client.list_stores()
            existing = next(
                (s for s in stores.stores if s.name == "securgraph"), None
            )
            if existing:
                store_id = existing.id
                print(f"  found existing OpenFGA store: {store_id}")
            else:
                resp = await temp_client.create_store(CreateStoreRequest(name="securgraph"))
                store_id = resp.id
                print(f"  created OpenFGA store: {store_id}")
        finally:
            await temp_client.close()

    config = build_openfga_configuration(settings)
    config.store_id = store_id
    config.authorization_model_id = model_id
    return OpenFgaClient(config)


async def _seed_fga_group_memberships(
    users: list[dict], groups: list[str], tenant_id: str
) -> None:
    """Seed FGA tuples: user → member → group for each user's groups."""
    from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

    fga = await _get_fga_client()
    tuples: list[ClientTuple] = []
    for user in users:
        user_id = user["id"]
        tuples.append(
            ClientTuple(
                user=f"user:{user_id}",
                relation="member",
                object=f"group:{tenant_authenticated_group_id(tenant_id)}",
            )
        )
        for group_id in user.get("groups", []):
            tuples.append(
                ClientTuple(
                    user=f"user:{user_id}",
                    relation="member",
                    object=f"group:{group_id}",
                )
            )
    written = 0
    for t in tuples:
        try:
            await fga.write(ClientWriteRequest(writes=[t]))
            written += 1
        except Exception:  # noqa: S110 - duplicate tuple, skip
            pass
    print(f"  seeded {written} group membership tuples")
    await fga.close()  # type: ignore[attr-defined]


async def _seed_fga_document_tuples(
    doc_ids: list[str], manifest: CorpusManifest
) -> None:
    """Seed FGA group_viewer and direct_viewer tuples per document.

    Owner tuples are already written by the API during ingestion. We only
    need to add group_viewer and direct_viewer tuples that the API does not
    write automatically.
    """
    from openfga_sdk.client.models import ClientTuple, ClientWriteRequest

    fga = await _get_fga_client()
    tuples: list[ClientTuple] = []
    for document_id, document in zip(doc_ids, manifest.documents, strict=True):
        if not document_id:
            continue
        target = f"document:{document_id}"
        # Skip owner tuples — the API writes them during ingestion.
        group_viewers = group_viewer_ids_for_document(
            tenant_id=manifest.tenant_id,
            classification=document.classification,
            manifest_groups=document.groups,
        )
        tuples.extend(
            ClientTuple(user=f"group:{group_id}#member", relation="group_viewer", object=target)
            for group_id in group_viewers
        )
        tuples.extend(
            ClientTuple(
                user=f"user:{user_id}", relation="direct_viewer", object=target
            )
            for user_id in document.direct_viewers
        )
    if not tuples:
        print("  (no group_viewer/direct_viewer tuples to seed)")
        await fga.close()  # type: ignore[attr-defined]
        return
    # Write in small batches to avoid rejecting the entire batch for one duplicate.
    batch_size = 10
    written = 0
    for i in range(0, len(tuples), batch_size):
        batch = tuples[i : i + batch_size]
        try:
            await fga.write(ClientWriteRequest(writes=batch))
            written += len(batch)
        except Exception as e:
            # Try individual tuples to skip duplicates.
            for t in batch:
                try:
                    await fga.write(ClientWriteRequest(writes=[t]))
                    written += 1
                except Exception:  # noqa: S110 - duplicate tuple, skip
                    pass
            if "already exists" not in str(e):
                print(f"  fga batch seed note: {e}")
    print(f"  seeded {written} group_viewer/direct_viewer tuples")
    await fga.close()  # type: ignore[attr-defined]


async def _ensure_tenant(tenant_id: str, groups: list[str]) -> None:
    """Create the tenant in PostgreSQL if it does not exist."""
    from uuid import uuid4

    from sqlalchemy import select

    from securgraph.api.dependencies import AsyncSessionLocal
    from securgraph.indexing.db import GroupRow, TenantRow

    display_name = "Helios Orion Lab" if tenant_id == "helios-orion-lab" else "MRD Lab"

    async with AsyncSessionLocal() as session:
        existing = await session.get(TenantRow, tenant_id)
        if existing is not None:
            print(f"  tenant {tenant_id} already exists")
            result = await session.execute(
                select(GroupRow).where(GroupRow.tenant_id == tenant_id)
            )
            existing_slugs = {g.slug for g in result.scalars().all()}
            for slug in groups:
                if slug not in existing_slugs:
                    session.add(
                        GroupRow(
                            id=f"grp_{uuid4().hex}",
                            tenant_id=tenant_id,
                            slug=slug,
                            display_name=slug.replace("-", " ").title(),
                            kind="functional",
                            is_active=True,
                        )
                    )
            await session.commit()
            return

        session.add(
            TenantRow(
                id=tenant_id,
                display_name=display_name,
                status="active",
                tenant_admin_settings_enabled=False,
            )
        )
        await session.commit()
        print(f"  created tenant {tenant_id}")

        for slug in groups:
            session.add(
                GroupRow(
                    id=f"grp_{uuid4().hex}",
                    tenant_id=tenant_id,
                    slug=slug,
                    display_name=slug.replace("-", " ").title(),
                    kind="functional",
                    is_active=True,
                )
            )
        await session.commit()
        print(f"  created {len(groups)} groups for tenant {tenant_id}")


async def _ingest_documents(manifest: CorpusManifest, *, api_base_url: str) -> list[str]:
    """Ingest all manifest documents via the HTTP API and return their doc_ids.

    Uses the live API so that chunk->parent_document FGA tuples are written
    by the API's own FGA client (direct ingest_document calls in a separate
    process would skip FGA tuple writes).
    """
    import httpx

    doc_ids: list[str] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        for document in manifest.documents:
            files = {
                "file": (
                    document.path.name,
                    document.path.read_bytes(),
                    "application/octet-stream",
                )
            }
            data = {
                "tenant_id": manifest.tenant_id,
                "owner_id": f"user:{document.owner}",
                "document_type": "other",
                "classification": document.classification,
                "policy_tags": ",".join(document.groups),
            }
            try:
                resp = await client.post(
                    f"{api_base_url.rstrip('/')}/api/v1/ingest",
                    files=files,
                    data=data,
                )
                body = resp.json() if resp.headers.get("content-type", "").startswith(
                    "application/json"
                ) else {}
                doc_id = body.get("document_id", "")
                status = body.get("status", "unknown")
                if resp.status_code in (200, 201):
                    doc_ids.append(doc_id)
                    print(f"  ingested {document.path.name} -> {doc_id} ({status})")
                else:
                    doc_ids.append("")
                    print(
                        f"  ingest failed {document.path.name}: "
                        f"HTTP {resp.status_code} {body.get('detail', '')}"
                    )
            except Exception as e:
                doc_ids.append("")
                print(f"  ingest error {document.path.name}: {e}")
    return doc_ids


async def _recompute_communities(tenant_id: str) -> None:
    """Run community recompute for the tenant."""
    from securgraph.adapters.community_generator import generate_hierarchical_communities
    from securgraph.adapters.neo4j import get_neo4j_driver
    from securgraph.api.dependencies import _fga_client  # type: ignore[attr-defined]

    driver = get_neo4j_driver()
    result = await generate_hierarchical_communities(
        driver, tenant_id=tenant_id, fga=_fga_client
    )
    print(f"  community recompute: {result}")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="mrd-seed-live")
    parser.add_argument("--corpus", type=Path, default=REPO_ROOT / "corpus" / "mrd_v1")
    parser.add_argument("--api-base-url", default="http://localhost:8000")
    parser.add_argument("--recompute-communities", action="store_true")
    args = parser.parse_args()

    manifest = load_corpus_manifest(args.corpus)
    users, groups = _load_principals(args.corpus)
    tenant_id = manifest.tenant_id

    print(
        f"Seeding MRD corpus: tenant={tenant_id}, "
        f"{len(users)} users, {len(groups)} groups, "
        f"{len(manifest.documents)} docs"
    )

    print("1. Ensuring tenant exists...")
    await _ensure_tenant(tenant_id, groups)

    print("2. Seeding FGA group memberships...")
    await _seed_fga_group_memberships(users, groups, tenant_id)

    print("3. Ingesting documents...")
    doc_ids = await _ingest_documents(manifest, api_base_url=args.api_base_url)

    print("4. Seeding FGA document access tuples...")
    await _seed_fga_document_tuples(doc_ids, manifest)

    if args.recompute_communities:
        print("5. Recomputing communities...")
        await _recompute_communities(tenant_id)

    valid_docs = sum(1 for d in doc_ids if d)
    print(f"\nSeed complete: {valid_docs}/{len(manifest.documents)} documents ingested.")
    if valid_docs < len(manifest.documents):
        print("WARNING: some documents failed to ingest. Check logs above.")
    print("Ready for live scoring with:")
    print(
        f"  .venv/bin/python eval/mrd_qa_smoke.py "
        f"--live-query-url {args.api_base_url.rstrip('/')}/api/v1/query "
        f"--tenant-id {tenant_id}"
    )


if __name__ == "__main__":
    asyncio.run(main())
