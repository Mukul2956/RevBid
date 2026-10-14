"""Demo mode: keep a handful of auctions open so a reviewer always finds something live.

Enabled with ``DEMO_MODE=true``. Whenever fewer than ``DEMO_MIN_OPEN_AUCTIONS`` auctions are
LIVE or SCHEDULED, new ones are scheduled for bikes that have no open auction. Idempotent and
safe across instances: the open-bike unique index rejects duplicates, and a race simply no-ops.
"""

import random
from datetime import timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.config import Settings
from app.domain.auction_state import AuctionStatus
from app.models.auction import Auction, AuctionEvent
from app.models.bike import Bike
from app.realtime.publisher import Publisher

log = structlog.stdlib.get_logger("app.demo")


# Rough INR starting prices by engine size, in paise.
def _starting_price(bike: Bike) -> int:
    cc = bike.engine_cc or 150
    base = 25_000 + cc * 150 - max(0, 2025 - bike.year) * 4_000
    return max(base, 15_000) // 500 * 500 * 100


async def ensure_demo_auctions(
    sessionmaker: async_sessionmaker[AsyncSession],
    clock: Clock,
    publisher: Publisher,
    settings: Settings,
) -> int:
    created = 0
    async with sessionmaker() as session:
        open_count = (
            await session.execute(
                select(func.count()).where(
                    Auction.status.in_([AuctionStatus.LIVE, AuctionStatus.SCHEDULED])
                )
            )
        ).scalar_one()
        missing = settings.demo_min_open_auctions - open_count
        if missing <= 0:
            return 0

        free_bikes = list(
            (
                await session.execute(
                    select(Bike)
                    .where(
                        ~Bike.id.in_(
                            select(Auction.bike_id).where(
                                Auction.status.in_(
                                    [
                                        AuctionStatus.DRAFT,
                                        AuctionStatus.SCHEDULED,
                                        AuctionStatus.LIVE,
                                    ]
                                )
                            )
                        )
                    )
                    .order_by(func.random())
                    .limit(missing)
                )
            ).scalars()
        )

    for bike in free_bikes:
        async with sessionmaker() as session:
            try:
                async with session.begin():
                    now = await clock.now(session)
                    # Stagger so some are live now (ending soon) and some are upcoming.
                    offset_min = random.choice([-10, -5, 0, 15, 45])  # noqa: S311
                    duration_min = random.choice([30, 60, 120, 240])  # noqa: S311
                    start = now + timedelta(minutes=offset_min)
                    price = _starting_price(bike)
                    auction = Auction(
                        bike_id=bike.id,
                        status=AuctionStatus.SCHEDULED,
                        start_at=start,
                        end_at=start + timedelta(minutes=duration_min),
                        original_end_at=start + timedelta(minutes=duration_min),
                        starting_price=price,
                        reserve_price=int(price * 1.25) // 50_000 * 50_000
                        if random.random() < 0.5  # noqa: S311
                        else None,
                        min_increment=max(price // 100 // 50_000 * 50_000, 50_000),
                        snipe_window_s=settings.default_snipe_window_s,
                        snipe_extension_s=settings.default_snipe_extension_s,
                        max_extension_s=settings.default_max_extension_s,
                        current_price=price,
                    )
                    session.add(auction)
                    await session.flush()
                    session.add(
                        AuctionEvent(
                            auction_id=auction.id,
                            type="created",
                            payload={"source": "demo"},
                            created_at=now,
                        )
                    )
                created += 1
            except IntegrityError:
                log.debug("demo_auction_race", bike_id=str(bike.id))
    if created:
        log.info("demo_auctions_created", count=created)
    return created
