from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Engine for writes and anything transactional; connections are health-checked on checkout."""
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # survive DB restarts / idle-connection culling on managed hosts
        pool_recycle=1800,
    )


def create_read_engine(settings: Settings) -> AsyncEngine:
    """Engine for read-only endpoints: AUTOCOMMIT, no pre-ping, its own small pool.

    Throughput of a request is bound by database round trips (docs/ARCHITECTURE.md, section 11).
    A pooled connection with the write engine's settings costs three before the query even runs:
    the pre-ping, BEGIN, and ROLLBACK. For reads all three can go: a read needs no transaction, and
    skipping the pre-ping is safe because reads are idempotent and SQLAlchemy invalidates the pool
    when a disconnect is detected; ``pool_recycle`` bounds how long an idle connection can live.

    (``pool_pre_ping`` and AUTOCOMMIT cannot be combined on this driver: the ping opens a real
    transaction, so BEGIN/ROLLBACK are sent regardless. A test asserts the wire behaviour.)
    The trade-off: consecutive statements in one request are not one snapshot, which is fine for
    independent list/detail reads. Writes never use this engine.
    """
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_read_pool_size,
        max_overflow=settings.db_read_max_overflow,
        pool_pre_ping=settings.db_read_pre_ping,
        pool_recycle=300,
        isolation_level="AUTOCOMMIT" if settings.db_read_autocommit else "READ COMMITTED",
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped session. Services open their own explicit transaction on it."""
    async with request.app.state.sessionmaker() as session:
        yield session


async def get_read_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Request-scoped AUTOCOMMIT session for endpoints that only read."""
    async with request.app.state.read_sessionmaker() as session:
        yield session
