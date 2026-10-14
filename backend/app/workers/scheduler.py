"""Background scheduler: drives lifecycle transitions and refreshes gauges.

Runs inside each API process (``RUN_SCHEDULER=true``) because the free hosting tier has one
service; it is safe to run in many processes at once and can be split into a dedicated worker by
flipping the flag. Correctness never depends on this loop being timely: bids are rejected after
``end_at`` by the bid path itself, and a restarted instance simply catches up on its first tick.
"""

import asyncio
import time
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import metrics
from app.core.clock import Clock
from app.core.config import Settings
from app.core.shutdown import run_bounded
from app.domain.auction_state import AuctionStatus
from app.models.auction import Auction
from app.realtime.publisher import Publisher
from app.services import lifecycle_service
from app.workers.demo import ensure_demo_auctions

log = structlog.stdlib.get_logger("app.scheduler")

GAUGE_REFRESH_EVERY_S = 10.0
DEMO_CHECK_EVERY_S = 30.0


@dataclass
class TickResult:
    promoted: int = 0
    closed: int = 0


class Scheduler:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        clock: Clock,
        publisher: Publisher,
        settings: Settings,
        stats: metrics.RuntimeStats,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock
        self._publisher = publisher
        self._settings = settings
        self._stats = stats
        self._task: asyncio.Task[None] | None = None
        self._last_gauges = 0.0
        self._last_demo = 0.0

    async def tick(self) -> TickResult:
        """One pass. Public so tests can drive the lifecycle deterministically."""
        batch = self._settings.scheduler_batch_size
        promoted = await lifecycle_service.promote_due(
            self._sessionmaker, self._clock, self._publisher, batch
        )
        closed = await lifecycle_service.close_due(
            self._sessionmaker, self._clock, self._publisher, batch
        )
        return TickResult(promoted=promoted, closed=closed)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="auction-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await run_bounded(self._task, budget_s=5.0, step="scheduler")

    async def _run(self) -> None:
        log.info("scheduler_started", interval_s=self._settings.scheduler_interval_s)
        while True:
            try:
                await self.tick()
                await self._housekeeping()
                now = time.time()
                metrics.SCHEDULER_LAST_TICK.set(now)
                self._stats.scheduler_last_tick = now
                self._stats.scheduler_last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                metrics.SCHEDULER_ERRORS.inc()
                self._stats.scheduler_last_error = f"{type(exc).__name__}: {exc}"
                log.exception("scheduler_tick_failed")
            await asyncio.sleep(self._settings.scheduler_interval_s)

    async def _housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_gauges >= GAUGE_REFRESH_EVERY_S:
            self._last_gauges = now
            await self._refresh_gauges()
        if self._settings.demo_mode and now - self._last_demo >= DEMO_CHECK_EVERY_S:
            self._last_demo = now
            await ensure_demo_auctions(
                self._sessionmaker, self._clock, self._publisher, self._settings
            )

    async def _refresh_gauges(self) -> None:
        async with self._sessionmaker() as session:
            rows = (
                await session.execute(select(Auction.status, func.count()).group_by(Auction.status))
            ).all()
        counts = dict(rows)
        for status in AuctionStatus:
            metrics.AUCTIONS_BY_STATUS.labels(status.value).set(counts.get(status.value, 0))
