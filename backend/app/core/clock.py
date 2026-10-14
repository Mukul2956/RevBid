"""Time source for auction logic.

The database is the single clock authority: with several API instances (and a client whose
clock may be wrong) only Postgres gives every instance the same notion of "now". Both the bid
path and the scheduler read time through a ``Clock`` so tests can substitute a controllable one.

Note ``clock_timestamp()`` rather than ``now()``: ``now()`` is frozen at transaction start,
which would be stale after a bid has waited on the auction's row lock.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class Clock(Protocol):
    async def now(self, session: AsyncSession) -> datetime:
        """Authoritative time (a database round trip). Use for any decision: bids, lifecycle."""
        ...

    def wall_time(self) -> datetime:
        """Cheap, no-I/O time for display-only fields such as ``server_time`` on read responses.

        Clients only use it to estimate their clock offset, so app-host/DB-host skew of a few
        milliseconds is irrelevant; it must never be used to decide anything.
        """
        ...


class DbClock:
    async def now(self, session: AsyncSession) -> datetime:
        result = await session.execute(text("SELECT clock_timestamp()"))
        value: datetime = result.scalar_one()
        return value

    def wall_time(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Deterministic clock for tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2030, 1, 1, 12, 0, tzinfo=UTC)

    async def now(self, session: AsyncSession) -> datetime:
        return self.current

    def wall_time(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> datetime:
        self.current += timedelta(**kwargs)
        return self.current
