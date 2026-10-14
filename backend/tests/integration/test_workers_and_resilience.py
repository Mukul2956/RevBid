import asyncio
from datetime import timedelta

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import func, select

from app.core import metrics
from app.core.clock import FakeClock
from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.core.shutdown import run_bounded
from app.domain.errors import RateLimited
from app.models.auction import Auction
from app.realtime.publisher import RecordingPublisher, RedisPublisher
from app.workers.demo import ensure_demo_auctions
from app.workers.scheduler import Scheduler
from tests.factories import auth, make_auction, make_bike, make_user
from tests.integration.test_bidding import bid


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class DeadRedis:
    """Every operation fails, like a Redis outage."""

    def pipeline(self, *a: object, **k: object) -> "DeadRedis":
        return self

    async def __aenter__(self) -> "DeadRedis":
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    def publish(self, *a: object) -> None:
        return None

    def set(self, *a: object, **k: object) -> None:
        return None

    def incr(self, *a: object) -> None:
        return None

    def ttl(self, *a: object) -> None:
        return None

    async def execute(self) -> None:
        raise ConnectionError("redis is down")


# --------------------------------------------------------------------------- resilience
async def test_redis_outage_never_fails_a_bid(client, app, clock, sessionmaker, settings) -> None:
    app.state.publisher = RedisPublisher(DeadRedis())  # type: ignore[arg-type]
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    before = sample("realtime_publish_failures_total")

    r = await bid(client, auth(settings, user), auction.id, auction.starting_price)

    assert r.status_code == 201, "the bid is durable even though nobody could be notified"
    assert sample("realtime_publish_failures_total") == before + 1
    async with sessionmaker() as s:
        assert (await s.get(Auction, auction.id)).bid_count == 1  # type: ignore[union-attr]


async def test_rate_limiter_fails_open_when_redis_is_down() -> None:
    limiter = RateLimiter(DeadRedis(), enabled=True)  # type: ignore[arg-type]
    before = sample("rate_limiter_errors_total")
    for _ in range(50):
        await limiter.hit(
            "login", "k", limit=1, window_s=60
        )  # would be rate limited if Redis worked
    assert sample("rate_limiter_errors_total") == before + 50


async def test_rate_limiter_counts_per_key_and_window(app) -> None:
    limiter = RateLimiter(app.state.redis, enabled=True)
    for _ in range(3):
        await limiter.hit("t", "alice", limit=3, window_s=60)
    with pytest.raises(RateLimited) as exc:
        await limiter.hit("t", "alice", limit=3, window_s=60)
    assert exc.value.details["retry_after"] >= 1
    await limiter.hit("t", "bob", limit=3, window_s=60)  # a different key is unaffected


async def test_rate_limiter_can_be_disabled() -> None:
    limiter = RateLimiter(DeadRedis(), enabled=False)  # type: ignore[arg-type]
    for _ in range(5):
        await limiter.hit("t", "k", limit=0, window_s=1)


async def test_run_bounded_abandons_a_stuck_step_instead_of_hanging() -> None:
    async def slow_to_cancel() -> None:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(1.0)  # cleanup that takes far longer than the step's budget

    started = asyncio.get_running_loop().time()
    await run_bounded(slow_to_cancel(), budget_s=0.2, step="slow")
    assert asyncio.get_running_loop().time() - started < 0.8, "must not wait for the cleanup"


async def test_run_bounded_swallows_and_logs_failures() -> None:
    async def explode() -> None:
        raise RuntimeError("nope")

    await run_bounded(explode(), budget_s=1, step="explode")  # must not raise
    await run_bounded(asyncio.sleep(0), budget_s=1, step="fine")


# --------------------------------------------------------------------------- scheduler loop
def make_scheduler(app, **overrides: object) -> Scheduler:  # type: ignore[no-untyped-def]
    s = app.state.settings
    settings = Settings(**{**s.model_dump(), "scheduler_interval_s": 0.05, **overrides})
    return Scheduler(
        sessionmaker=app.state.sessionmaker,
        clock=app.state.clock,
        publisher=RecordingPublisher(),
        settings=settings,
        stats=metrics.RuntimeStats(),
    )


async def test_background_loop_settles_auctions_and_reports_liveness(
    app, clock: FakeClock, sessionmaker
) -> None:
    auction = await make_auction(sessionmaker, now=clock.current)
    clock.current = auction.end_at + timedelta(seconds=1)
    scheduler = make_scheduler(app)
    stats = scheduler._stats

    scheduler.start()
    try:
        for _ in range(100):
            async with sessionmaker() as s:
                ended = (await s.get(Auction, auction.id)).status == "ENDED"  # type: ignore[union-attr]
            # last_tick is recorded after the tick completes, so wait for it as well
            if ended and stats.scheduler_last_tick is not None:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("loop did not close the overdue auction and report a completed tick")
        assert stats.scheduler_last_error is None
    finally:
        await scheduler.stop()


async def test_loop_survives_a_failing_tick_and_records_the_error(app, monkeypatch) -> None:
    scheduler = make_scheduler(app)
    calls = {"n": 0}
    real_tick = scheduler.tick

    async def flaky():  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database blip")
        return await real_tick()

    monkeypatch.setattr(scheduler, "tick", flaky)
    before = sample("scheduler_errors_total")
    scheduler.start()
    try:
        for _ in range(100):
            if calls["n"] >= 3:
                break
            await asyncio.sleep(0.05)
        assert calls["n"] >= 3, "loop must keep ticking after an error"
        assert sample("scheduler_errors_total") == before + 1
        assert scheduler._stats.scheduler_last_error is None, "cleared by the next successful tick"
    finally:
        await scheduler.stop()


async def test_status_gauges_reflect_the_database(app, clock, sessionmaker) -> None:
    await make_auction(sessionmaker, now=clock.current)
    await make_auction(sessionmaker, now=clock.current)
    await make_auction(
        sessionmaker, now=clock.current, status="SCHEDULED", start_in=timedelta(hours=1)
    )
    await make_scheduler(app)._refresh_gauges()
    assert sample("auctions", status="LIVE") == 2
    assert sample("auctions", status="SCHEDULED") == 1
    assert sample("auctions", status="ENDED") == 0


async def test_closing_records_scheduler_lag(app, clock: FakeClock, sessionmaker) -> None:
    auction = await make_auction(sessionmaker, now=clock.current)
    clock.current = auction.end_at + timedelta(seconds=7)
    before = sample("scheduler_lag_seconds_count")
    await make_scheduler(app).tick()
    assert sample("scheduler_lag_seconds_count") == before + 1
    assert sample("scheduler_lag_seconds_sum") >= 7


# --------------------------------------------------------------------------- demo mode
async def test_demo_mode_keeps_auctions_open_and_is_idempotent(
    app, clock, sessionmaker, settings
) -> None:
    for i in range(6):
        await make_bike(sessionmaker, model=f"Bike {i}", engine_cc=150 + 100 * i, year=2018 + i % 5)
    settings = Settings(**{**settings.model_dump(), "demo_min_open_auctions": 4})

    created = await ensure_demo_auctions(sessionmaker, clock, RecordingPublisher(), settings)
    again = await ensure_demo_auctions(sessionmaker, clock, RecordingPublisher(), settings)

    assert (created, again) == (4, 0)
    async with sessionmaker() as s:
        rows = (await s.execute(select(Auction))).scalars().all()
    assert len(rows) == 4 and len({a.bike_id for a in rows}) == 4, "one open auction per bike"
    assert all(a.status == "SCHEDULED" and a.current_price == a.starting_price for a in rows)
    assert all(a.reserve_price is None or a.reserve_price >= a.starting_price for a in rows)


async def test_demo_mode_replenishes_after_auctions_end(
    app, clock: FakeClock, sessionmaker, settings
) -> None:
    for i in range(8):
        await make_bike(sessionmaker, model=f"Bike {i}")
    settings = Settings(**{**settings.model_dump(), "demo_min_open_auctions": 3})
    await ensure_demo_auctions(sessionmaker, clock, RecordingPublisher(), settings)

    clock.advance(hours=6)  # outlasts any demo auction (offset <= 45 min, duration <= 4 h)
    await app.state.scheduler.tick()  # everything started and ended
    created = await ensure_demo_auctions(sessionmaker, clock, RecordingPublisher(), settings)

    assert created == 3
    async with sessionmaker() as s:
        open_count = (
            await s.execute(select(func.count()).where(Auction.status.in_(["SCHEDULED", "LIVE"])))
        ).scalar_one()
    assert open_count == 3


async def test_demo_mode_with_no_free_bikes_is_a_noop(clock, sessionmaker, settings) -> None:
    assert await ensure_demo_auctions(sessionmaker, clock, RecordingPublisher(), settings) == 0
