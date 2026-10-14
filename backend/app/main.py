"""Application factory and process lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.router import api_router
from app.api.v1 import health
from app.core.clock import Clock, DbClock
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.metrics import RuntimeStats
from app.core.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.rate_limit import RateLimiter
from app.core.shutdown import run_bounded
from app.db.session import create_engine, create_read_engine, create_sessionmaker
from app.realtime import ws
from app.realtime.hub import Hub
from app.realtime.publisher import Publisher, RedisPublisher
from app.workers.scheduler import Scheduler

log = structlog.stdlib.get_logger("app")

DESCRIPTION = """
Live auctions for used motorcycles.

* **REST** under `/api/v1` — browse auctions, place bids, administer inventory.
* **WebSocket** at `/ws` — live bid/lifecycle events (see `app/realtime/ws.py` for the protocol).
* **Ops** — `/healthz`, `/readyz`, `/metrics`.

Money is always **integer paise** (1 INR = 100 paise). Errors are RFC 7807 problem documents with
a stable `code`.
"""


def create_app(
    settings: Settings | None = None,
    *,
    clock: Clock | None = None,
    publisher: Publisher | None = None,
) -> FastAPI:
    """Build the app. ``clock``/``publisher`` can be injected (tests); defaults are production."""
    settings = settings or get_settings()
    configure_logging(settings)
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.env,
            release=settings.app_version,
            traces_sample_rate=0.05,
            send_default_pii=False,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        sessionmaker = create_sessionmaker(engine)
        read_engine = create_read_engine(settings)
        read_sessionmaker = create_sessionmaker(read_engine)
        redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=30,  # detect half-open connections (pub/sub especially)
            socket_keepalive=True,
        )
        stats = RuntimeStats()
        the_clock = clock or DbClock()
        the_publisher = publisher or RedisPublisher(redis)
        hub = Hub(redis, settings)

        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.read_sessionmaker = read_sessionmaker
        app.state.redis = redis
        app.state.clock = the_clock
        app.state.publisher = the_publisher
        app.state.stats = stats
        app.state.limiter = RateLimiter(redis, settings.rate_limit_enabled)
        app.state.hub = hub

        scheduler = Scheduler(
            sessionmaker=sessionmaker,
            clock=the_clock,
            publisher=the_publisher,
            settings=settings,
            stats=stats,
        )
        app.state.scheduler = scheduler

        await hub.start()
        if settings.run_scheduler:
            scheduler.start()
        log.info("app_started", env=settings.env, version=settings.app_version)
        try:
            yield
        finally:
            log.info("app_stopping")
            await scheduler.stop()
            await hub.stop()
            await run_bounded(redis.aclose(), budget_s=3.0, step="redis_close")
            await run_bounded(engine.dispose(), budget_s=5.0, step="engine_dispose")
            await run_bounded(read_engine.dispose(), budget_s=5.0, step="read_engine_dispose")

    app = FastAPI(
        title="Bike Auction API",
        version=settings.app_version,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/docs",
        redoc_url=None,
    )

    # Middleware runs outermost-first in reverse order of registration: RequestContext sees
    # everything (and stamps the request id on it), CORS answers preflights, headers go last.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.enable_hsts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(api_router)
    app.include_router(ws.router)
    return app
