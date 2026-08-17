"""Async engine and session factory.

One engine per process; sessions are request-scoped and injected via
app.api.deps.get_db. Nothing outside this module creates sessions.
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.url import is_sqlite

_settings = get_settings()

# SQLite (used for fast unit tests) has no connection pool concept and
# rejects pool_size/max_overflow entirely; only pass them for real
# server-based databases (Postgres in dev/staging/prod).
#
# pool_recycle matters on serverless Postgres (Neon and friends), which drops
# idle connections server-side; without it the pool hands out sockets the
# server has already closed and the first query of a quiet period fails.
_pool_kwargs = (
    {} if is_sqlite(_settings.database_url)
    else {"pool_size": _settings.database_pool_size,
          "max_overflow": _settings.database_max_overflow,
          "pool_recycle": 300}
)

engine = create_async_engine(
    # Normalised, not raw: managed providers issue libpq URLs that asyncpg
    # cannot parse. See app/db/url.py.
    _settings.async_database_url,
    pool_pre_ping=True,
    connect_args=_settings.database_connect_args,
    **_pool_kwargs,
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session with commit-on-success, rollback-on-error semantics."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
