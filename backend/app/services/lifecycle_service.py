"""Time-driven lifecycle transitions: SCHEDULED -> LIVE and LIVE -> ENDED.

Safe with any number of API instances running the scheduler: candidates are claimed with
``FOR UPDATE SKIP LOCKED`` (an instance never waits on, or double-processes, a row another one
holds) and each auction is handled in its own short transaction.

Close vs. last-second bid
-------------------------
Closing takes the *same* row lock a bid takes. Whichever transaction gets the lock first wins:

* bid first  -> it may extend ``end_at``; the closer's ``WHERE end_at <= now`` then no longer
  matches the (re-evaluated) row, so the auction stays open;
* close first -> the bid blocks, then sees status ENDED and is rejected.

If a bid holds the lock while the closer polls, ``SKIP LOCKED`` skips the row and the next tick
(one interval later) re-examines it. No interleaving can both extend and close an auction.
"""

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import metrics
from app.core.clock import Clock
from app.domain.auction_state import AuctionStatus, assert_transition, resolve_outcome
from app.domain.masking import bidder_alias
from app.models.auction import Auction, AuctionEvent
from app.realtime.events import ROOM_LOBBY, bike_title, build_event, room_auction, room_user
from app.realtime.publisher import Publisher

log = structlog.stdlib.get_logger("app.lifecycle")

Events = list[tuple[str, dict[str, Any]]]


async def promote_due(
    sessionmaker: async_sessionmaker[AsyncSession],
    clock: Clock,
    publisher: Publisher,
    limit: int = 50,
) -> int:
    """SCHEDULED -> LIVE for every auction whose start time has arrived."""
    events: Events = []
    promoted = 0
    async with sessionmaker() as session, session.begin():
        now = await clock.now(session)
        due = (
            (
                await session.execute(
                    select(Auction)
                    .where(Auction.status == AuctionStatus.SCHEDULED, Auction.start_at <= now)
                    .order_by(Auction.start_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True, of=Auction)
                )
            )
            .scalars()
            .all()
        )
        for auction in due:
            assert_transition(auction.status, AuctionStatus.LIVE)
            auction.status = AuctionStatus.LIVE
            auction.version += 1
            session.add(
                AuctionEvent(auction_id=auction.id, type="started", payload={}, created_at=now)
            )
            metrics.AUCTIONS_STARTED.inc()
            promoted += 1
            log.info("auction_started", auction_id=str(auction.id))
            event = build_event(
                "auction_started",
                auction=auction,
                server_time=now,
                data={"status": auction.status, "end_at": auction.end_at},
            )
            events += [(room_auction(auction.id), event), (ROOM_LOBBY, event)]
    await publisher.publish_many(events)
    return promoted


async def close_due(
    sessionmaker: async_sessionmaker[AsyncSession],
    clock: Clock,
    publisher: Publisher,
    limit: int = 50,
) -> int:
    """LIVE -> ENDED for every auction whose (possibly extended) end time has passed."""
    closed = 0
    for _ in range(limit):
        events: Events = []
        async with sessionmaker() as session, session.begin():
            now = await clock.now(session)
            auction = (
                await session.execute(
                    select(Auction)
                    .where(Auction.status == AuctionStatus.LIVE, Auction.end_at <= now)
                    .order_by(Auction.end_at)
                    .limit(1)
                    .with_for_update(skip_locked=True, of=Auction)
                )
            ).scalar_one_or_none()
            if auction is None:
                break

            assert_transition(auction.status, AuctionStatus.ENDED)
            outcome, winner_id = resolve_outcome(
                bid_count=auction.bid_count,
                current_price=auction.current_price,
                reserve_price=auction.reserve_price,
                leader_id=auction.leader_id,
            )
            lag = max((now - auction.end_at).total_seconds(), 0.0)
            auction.status = AuctionStatus.ENDED
            auction.outcome = outcome
            auction.winner_id = winner_id
            auction.ended_at = now
            auction.version += 1
            session.add(
                AuctionEvent(
                    auction_id=auction.id,
                    type="ended",
                    payload={
                        "outcome": outcome,
                        "final_price": auction.current_price,
                        "bid_count": auction.bid_count,
                        "lag_s": round(lag, 3),
                    },
                    created_at=now,
                )
            )
            metrics.AUCTIONS_CLOSED.labels(str(outcome)).inc()
            metrics.SCHEDULER_LAG.observe(lag)
            log.info(
                "auction_ended",
                auction_id=str(auction.id),
                outcome=str(outcome),
                final_price=auction.current_price,
                bid_count=auction.bid_count,
                lag_s=round(lag, 3),
            )
            events = _ended_events(auction, now, winner_id)
        await publisher.publish_many(events)
        closed += 1
    return closed


def _ended_events(auction: Auction, now: Any, winner_id: uuid.UUID | None) -> Events:
    public = build_event(
        "auction_ended",
        auction=auction,
        server_time=now,
        data={
            "status": auction.status,
            "outcome": auction.outcome,
            "final_price": auction.current_price,
            "winner_alias": bidder_alias(auction.id, winner_id) if winner_id else None,
        },
    )
    items: Events = [(room_auction(auction.id), public), (ROOM_LOBBY, public)]
    if winner_id is not None:
        items.append(
            (
                room_user(winner_id),
                build_event(
                    "auction_won",
                    auction=auction,
                    server_time=now,
                    data={"title": bike_title(auction), "final_price": auction.current_price},
                ),
            )
        )
    return items
