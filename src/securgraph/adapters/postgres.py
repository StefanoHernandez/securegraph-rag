"""PostgreSQL async engine + session factory.

Single source of truth for the database connection. Other modules import
``get_engine()`` and ``session_factory()`` from here. Idempotent: repeated
calls reuse the same engine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from securgraph.config import Settings, get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Process-wide async engine. One per Settings.postgres_dsn."""
    settings = get_settings()
    return _build_engine(settings)


def _build_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.postgres_dsn,
        echo=False,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )


@lru_cache(maxsize=1)
def session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a transactional ``AsyncSession`` and commit/rollback on exit."""
    factory = session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def reset_engine_cache() -> None:
    """Drop the cached engine — needed in tests that swap DSNs."""
    get_engine.cache_clear()
    session_factory.cache_clear()
