"""Test harness.

* Real PostgreSQL + Redis (no SQLite, no mocks): the behaviours under test (row locks, partial
  unique indexes, SKIP LOCKED, pub/sub) only exist in the real thing.
* Schema is built by running the actual Alembic migrations, so migrations are tested too.
* A ``FakeClock`` drives every time-dependent rule deterministically (no sleeping).
* One event loop for the whole session (asyncpg pools are bound to a loop).
"""

import asyncio
import os
from collections.abc import AsyncIterator

# Environment must be set before any `app` module reads settings (alembic's env.py does too).
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://auction:auction@127.0.0.1:5433/auction_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6380/15")
os.environ.update(
    {
        "ENV": "test",
        "DATABASE_URL": TEST_DATABASE_URL,
        "REDIS_URL": TEST_REDIS_URL,
        "RUN_SCHEDULER": "false",  # tests drive scheduler.tick() explicitly
        "RATE_LIMIT_ENABLED": "false",
        "LOG_LEVEL": "WARNING",
        "JWT_SECRET": "test-secret-test-secret-test-secret-0123456789",
    }
)

import asyncpg  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from alembic.config import Config  # noqa: E402
from asgi_lifespan import LifespanManager  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker  # noqa: E402

from alembic import command  # noqa: E402
from app.core.clock import FakeClock  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.db.session import create_engine, create_sessionmaker  # noqa: E402
from app.main import create_app  # noqa: E402
from app.realtime.publisher import RecordingPublisher  # noqa: E402

get_settings.cache_clear()


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _ensure_database() -> None:
    """Create the test database if the dev volume predates the init script."""
    dsn = _asyncpg_dsn(TEST_DATABASE_URL)
    name = dsn.rsplit("/", 1)[1]
    try:
        conn = await asyncpg.connect(dsn)
    except asyncpg.InvalidCatalogNameError:
        admin = await asyncpg.connect(dsn.rsplit("/", 1)[0] + "/postgres")
        await admin.execute(f'CREATE DATABASE "{name}"')
        await admin.close()
        return
    await conn.close()


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings()


@pytest_asyncio.fixture(scope="session")
async def _database_schema() -> None:
    await _ensure_database()
    conn = await asyncpg.connect(_asyncpg_dsn(TEST_DATABASE_URL))
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await conn.close()
    cfg = Config("alembic.ini")
    # Alembic's async env calls asyncio.run(); do that off the running loop.
    await asyncio.to_thread(command.upgrade, cfg, "head")


@pytest_asyncio.fixture(scope="session")
async def engine(_database_schema: None, settings: Settings) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(settings)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(engine)


_TABLES = "auction_events, bids, auctions, bikes, refresh_tokens, users"


@pytest_asyncio.fixture
async def _clean_state(engine: AsyncEngine) -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.flushdb()
    await redis.aclose()
    yield


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest_asyncio.fixture
async def app(
    settings: Settings, clock: FakeClock, publisher: RecordingPublisher
) -> AsyncIterator[FastAPI]:
    """App with a fake clock and a recording publisher (events asserted, not delivered)."""
    application = create_app(settings, clock=clock, publisher=publisher)
    async with LifespanManager(application):
        yield application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def live_app(settings: Settings, clock: FakeClock) -> AsyncIterator[FastAPI]:
    """App with the *real* Redis publisher, so events travel Redis -> hub -> WebSocket."""
    application = create_app(settings, clock=clock)
    async with LifespanManager(application):
        await asyncio.wait_for(application.state.hub.ready.wait(), timeout=5)
        yield application


@pytest_asyncio.fixture
async def real_clock_app(settings: Settings) -> AsyncIterator[FastAPI]:
    """App on the database clock, for genuine race tests (no fake time)."""
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture(autouse=True)
def _no_leaked_settings_cache() -> None:
    get_settings.cache_clear()
