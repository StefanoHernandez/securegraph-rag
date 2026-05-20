"""Neo4j AsyncDriver connection management.

Process-wide cached singleton driver instance.
"""

from __future__ import annotations

from functools import lru_cache

from neo4j import AsyncDriver, AsyncGraphDatabase

from securgraph.config import Settings, get_settings


@lru_cache(maxsize=1)
def get_neo4j_driver() -> AsyncDriver:
    """Process-wide Neo4j async driver instance. One per Settings.neo4j_uri."""
    settings = get_settings()
    return _build_driver(settings)


def _build_driver(settings: Settings) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )


async def close_neo4j_driver() -> None:
    """Close the process-wide Neo4j async driver."""
    # Retrieve the cached driver without rebuilding
    driver = get_neo4j_driver()
    await driver.close()
    get_neo4j_driver.cache_clear()


def reset_neo4j_driver_cache() -> None:
    """Drop the cached driver — needed in tests that swap configuration."""
    get_neo4j_driver.cache_clear()
