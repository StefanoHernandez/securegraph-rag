"""API dependencies.

Provides reusable async database sessions and other API-level resources.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import structlog
from arq.connections import ArqRedis, RedisSettings, create_pool
from fastapi import Depends, HTTPException
from openfga_sdk import ClientConfiguration, OpenFgaClient
from openfga_sdk.models import CreateStoreRequest, TypeDefinition, WriteAuthorizationModelRequest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from securgraph.config import Settings, get_settings
from securgraph.domain.policy import OpenFGAPolicyAdapter

if TYPE_CHECKING:
    from securgraph.indexing.graph_retriever import AuthorizedGraphRetriever
    from securgraph.indexing.vector_retriever import QdrantRetriever

logger = structlog.get_logger(__name__)

settings = get_settings()

# Async engine configured using DSN from central Settings
engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Singletons or cache for OpenFGA client, adapter, and Redis pool
_fga_client: OpenFgaClient | None = None
_policy_adapter: OpenFGAPolicyAdapter | None = None
_redis_pool: ArqRedis | None = None


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Provide a transactional async database session.

    Rolls back automatically in the event of an unhandled exception.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def set_tenant_rls_context(
    session: AsyncSession, *, tenant_id: str | None, is_super_admin: bool
) -> None:
    """Bind PostgreSQL RLS settings to current transaction after authentication."""
    await session.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.is_super_admin', :is_super_admin, true)"
        ),
        {
            "tenant_id": tenant_id or "",
            "is_super_admin": "true" if is_super_admin else "false",
        },
    )


async def bootstrap_openfga(app_settings: Settings) -> None:
    """Bootstraps OpenFGA: creates store and applies authorization model if needed."""
    global _fga_client, _policy_adapter

    config = ClientConfiguration(
        api_url=app_settings.openfga_api_url,
        store_id=app_settings.openfga_store_id or None,
        authorization_model_id=app_settings.openfga_authorization_model_id or None,
    )
    _fga_client = OpenFgaClient(config)
    _policy_adapter = OpenFGAPolicyAdapter(_fga_client)

    # If store ID is not configured, dynamically search or create "securgraph" store
    if not app_settings.openfga_store_id:
        logger.info("OpenFGA store ID is blank. Attempting auto-bootstrapping.")
        try:
            stores_response = await _fga_client.list_stores()
            existing_store = None
            if stores_response and stores_response.stores:
                for s in stores_response.stores:
                    if s.name == "securgraph":
                        existing_store = s
                        break

            if existing_store:
                store_id = existing_store.id
                logger.info(
                    "Found existing OpenFGA store", store_name="securgraph", store_id=store_id
                )
            else:
                logger.info("Creating new OpenFGA store", store_name="securgraph")
                create_response = await _fga_client.create_store(
                    CreateStoreRequest(name="securgraph")  # type: ignore[no-untyped-call]
                )
                store_id = create_response.id
                logger.info("Created OpenFGA store", store_name="securgraph", store_id=store_id)

            app_settings.openfga_store_id = store_id
            _fga_client.set_store_id(store_id)  # type: ignore[no-untyped-call]

        except Exception as e:
            logger.error("Failed to bootstrap OpenFGA store", error=str(e))
            if app_settings.env == "test":
                return
            raise e

    # If authorization model ID is not configured, write our model
    if app_settings.openfga_store_id and not app_settings.openfga_authorization_model_id:
        logger.info("OpenFGA authorization model ID is blank. Writing schema.")
        try:
            type_definitions = [
                {"type": "user"},
                {
                    "type": "group",
                    "relations": {
                        "member": {
                            "this": {},
                            "allowed_types": [
                                {"type": "user"},
                                {"type": "group", "relation": "member"},
                            ],
                        }
                    },
                    "metadata": {
                        "relations": {
                            "member": {
                                "directly_related_user_types": [
                                    {"type": "user"},
                                    {"type": "group", "relation": "member"},
                                ]
                            }
                        }
                    },
                },
                {
                    "type": "document",
                    "relations": {
                        "owner": {"this": {}, "allowed_types": [{"type": "user"}]},
                        "group_viewer": {
                            "this": {},
                            "allowed_types": [{"type": "group", "relation": "member"}],
                        },
                        "direct_viewer": {"this": {}, "allowed_types": [{"type": "user"}]},
                        "viewer": {
                            "union": {
                                "child": [
                                    {"computedUserset": {"relation": "owner"}},
                                    {"computedUserset": {"relation": "group_viewer"}},
                                    {"computedUserset": {"relation": "direct_viewer"}},
                                ]
                            }
                        },
                    },
                    "metadata": {
                        "relations": {
                            "owner": {"directly_related_user_types": [{"type": "user"}]},
                            "group_viewer": {
                                "directly_related_user_types": [
                                    {"type": "group", "relation": "member"}
                                ]
                            },
                            "direct_viewer": {"directly_related_user_types": [{"type": "user"}]},
                            "viewer": {"directly_related_user_types": []},
                        }
                    },
                },
                {
                    "type": "chunk",
                    "relations": {
                        "parent_document": {"this": {}, "allowed_types": [{"type": "document"}]},
                        "viewer": {
                            "tupleToUserset": {
                                "tupleset": {"relation": "parent_document"},
                                "computedUserset": {"relation": "viewer"},
                            }
                        },
                    },
                    "metadata": {
                        "relations": {
                            "parent_document": {
                                "directly_related_user_types": [{"type": "document"}]
                            },
                            "viewer": {"directly_related_user_types": []},
                        }
                    },
                },
                {
                    "type": "policy_class",
                    "relations": {
                        "owner": {"this": {}, "allowed_types": [{"type": "user"}]},
                        "group_viewer": {
                            "this": {},
                            "allowed_types": [{"type": "group", "relation": "member"}],
                        },
                        "direct_viewer": {"this": {}, "allowed_types": [{"type": "user"}]},
                        "viewer": {
                            "union": {
                                "child": [
                                    {"computedUserset": {"relation": "owner"}},
                                    {"computedUserset": {"relation": "group_viewer"}},
                                    {"computedUserset": {"relation": "direct_viewer"}},
                                ]
                            }
                        },
                    },
                    "metadata": {
                        "relations": {
                            "owner": {"directly_related_user_types": [{"type": "user"}]},
                            "group_viewer": {
                                "directly_related_user_types": [
                                    {"type": "group", "relation": "member"}
                                ]
                            },
                            "direct_viewer": {"directly_related_user_types": [{"type": "user"}]},
                            "viewer": {"directly_related_user_types": []},
                        }
                    },
                },
                {
                    "type": "community",
                    "relations": {
                        "in_class": {"this": {}, "allowed_types": [{"type": "policy_class"}]},
                        "viewer": {
                            "tupleToUserset": {
                                "tupleset": {"relation": "in_class"},
                                "computedUserset": {"relation": "viewer"},
                            }
                        },
                    },
                    "metadata": {
                        "relations": {
                            "in_class": {"directly_related_user_types": [{"type": "policy_class"}]},
                            "viewer": {"directly_related_user_types": []},
                        }
                    },
                },
            ]

            defs = [TypeDefinition(**td) for td in type_definitions]  # type: ignore[no-untyped-call, arg-type]
            req = WriteAuthorizationModelRequest(type_definitions=defs, schema_version="1.1")  # type: ignore[no-untyped-call]
            response = await _fga_client.write_authorization_model(req)
            model_id = response.authorization_model_id
            logger.info("Successfully wrote OpenFGA authorization model", model_id=model_id)

            app_settings.openfga_authorization_model_id = model_id
            _fga_client.set_authorization_model_id(model_id)  # type: ignore[no-untyped-call]

        except Exception as e:
            logger.error("Failed to write OpenFGA authorization model", error=str(e))
            if app_settings.env == "test":
                return
            raise e


async def get_openfga_client() -> OpenFgaClient:
    """Dependency provider for OpenFgaClient."""
    global _fga_client
    if _fga_client is None:
        app_settings = get_settings()
        config = ClientConfiguration(
            api_url=app_settings.openfga_api_url,
            store_id=app_settings.openfga_store_id or None,
            authorization_model_id=app_settings.openfga_authorization_model_id or None,
        )
        _fga_client = OpenFgaClient(config)
    return _fga_client


async def get_policy_adapter(
    client: OpenFgaClient = Depends(get_openfga_client),
) -> OpenFGAPolicyAdapter:
    """Dependency provider for OpenFGAPolicyAdapter."""
    global _policy_adapter
    if _policy_adapter is None or _policy_adapter.client is not client:
        _policy_adapter = OpenFGAPolicyAdapter(client)
    return _policy_adapter


# ---------------------------------------------------------------------------
# Retriever singletons (initialised lazily on first request)
# ---------------------------------------------------------------------------

_qdrant_retriever: QdrantRetriever | None = None
_graph_retriever: AuthorizedGraphRetriever | None = None


async def get_qdrant_retriever() -> QdrantRetriever:
    """Return a cached QdrantRetriever singleton.

    Uses the process-wide embedding adapter and a QdrantClient pointed at the
    URL from Settings.  Safe to call from multiple concurrent requests.
    """
    global _qdrant_retriever
    if _qdrant_retriever is None:
        from qdrant_client import QdrantClient

        from securgraph.adapters.embeddings import get_embedding_adapter
        from securgraph.indexing.vector_retriever import QdrantRetriever

        _settings = get_settings()
        client = QdrantClient(url=_settings.qdrant_url)
        _qdrant_retriever = QdrantRetriever(client, get_embedding_adapter())
    return _qdrant_retriever


async def get_graph_retriever(
    policy: OpenFGAPolicyAdapter = Depends(get_policy_adapter),
) -> AuthorizedGraphRetriever:
    """Return a cached AuthorizedGraphRetriever singleton.

    Combines the Neo4j driver with the OpenFGA policy adapter to enforce
    path-cut pruning during graph traversal.
    """
    global _graph_retriever
    if _graph_retriever is None:
        from securgraph.adapters.neo4j import get_neo4j_driver
        from securgraph.indexing.graph_retriever import AuthorizedGraphRetriever

        _graph_retriever = AuthorizedGraphRetriever(driver=get_neo4j_driver(), fga=policy)
    return _graph_retriever


async def get_redis_pool() -> ArqRedis:
    """Return the global cached Redis pool instance used by arq."""
    global _redis_pool
    if _redis_pool is None:
        _settings = get_settings()
        _redis_pool = await create_pool(RedisSettings.from_dsn(_settings.redis_url))
    return _redis_pool


async def get_redis_pool_optional() -> ArqRedis | None:
    """Redis pool for routes that only need it when async workers are enabled.

    With ``feature_workers_enabled=False`` (the default) ingestion runs
    in-process and Redis is not part of the stack (compose profile
    ``workers`` is opt-in) — connecting eagerly would turn every upload into
    a 500. Returns None without touching the network in that case; when the
    flag is ON, a down Redis surfaces as an explicit 503 instead of a
    generic connection error.
    """
    if not get_settings().feature_workers_enabled:
        return None
    try:
        return await get_redis_pool()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Async ingestion workers are enabled but the Redis queue is unreachable.",
        ) from exc
