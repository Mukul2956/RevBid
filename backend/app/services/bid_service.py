"""Bid placement: the hot path, and the part of the system that must never be wrong.

Concurrency model
-----------------
One transaction per bid. The first statement locks the auction row (``SELECT ... FOR UPDATE``).
That serialises all bids on *one* auction while leaving other auctions fully parallel. Everything
the rules need (price, leader, end time) is read under that lock, so a bid is validated against
the true latest state; there is no check-then-act race to reason about.

Defence in depth behind the lock: ``UNIQUE(auction_id, amount)`` makes duplicate winning amounts
impossible at the schema level, and ``UNIQUE(bidder_id, idempotency_key)`` makes retries safe.

Time comes from the database *after* the lock is held (see ``core.clock``).

Realtime events are published only after commit, best-effort (see ``realtime.publisher``).
"""

import time
import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import metrics
from app.core.clock import Clock
from app.core.config import Settings
from app.core.security import Principal
from app.domain.auction_state import reserve_met
from app.domain.bidding_rules import BidContext, compute_new_end, validate_bid
from app.domain.errors import DomainError, IdempotencyConflict, NotFound
from app.domain.masking import bidder_alias
from app.models.auction import Auction, AuctionEvent, Bid
from app.realtime.events import ROOM_LOBBY, bike_title, build_event, room_auction, room_user
from app.realtime.publisher import Publisher
from app.schemas.auction import BidAccepted
from app.services import presenters

log = structlog.stdlib.get_logger("app.bids")


def _context(a: Auction) -> BidContext:
    return BidContext(
        status=a.status,
        end_at=a.end_at,
        original_end_at=a.original_end_at,
        starting_price=a.starting_price,
        current_price=a.current_price,
        min_increment=a.min_increment,
        bid_count=a.bid_count,
        leader_id=a.leader_id,
        snipe_window_s=a.snipe_window_s,
        snipe_extension_s=a.snipe_extension_s,
        max_extension_s=a.max_extension_s,
    )


async def place_bid(
    session: AsyncSession,
    *,
    clock: Clock,
    publisher: Publisher,
    settings: Settings,
    stats: metrics.RuntimeStats,
    auction_id: uuid.UUID,
    bidder: Principal,
    amount: int,
    idempotency_key: str | None = None,
) -> BidAccepted:
    structlog.contextvars.bind_contextvars(auction_id=str(auction_id), user_id=str(bidder.id))
    started = time.perf_counter()
    try:
        outcome = await _place_bid_tx(
            session,
            clock=clock,
            settings=settings,
            auction_id=auction_id,
            bidder=bidder,
            amount=amount,
            idempotency_key=idempotency_key,
        )
    except DomainError as exc:
        metrics.BIDS.labels(exc.code).inc()
        log.info("bid_rejected", code=exc.code, amount=amount, **exc.details)
        raise
    finally:
        metrics.BID_DURATION.observe(time.perf_counter() - started)

    if outcome.replayed:  # a retried request: nothing new happened, so nothing to count or publish
        metrics.BIDS.labels("replayed").inc()
        return outcome.response

    metrics.BIDS.labels("accepted").inc()
    stats.record_bid()
    await publisher.publish_many(outcome.events)
    return outcome.response


class _TxOutcome:
    def __init__(
        self,
        response: BidAccepted,
        events: list[tuple[str, dict[str, Any]]],
        *,
        replayed: bool = False,
    ) -> None:
        self.response = response
        self.events = events
        self.replayed = replayed


async def _place_bid_tx(
    session: AsyncSession,
    *,
    clock: Clock,
    settings: Settings,
    auction_id: uuid.UUID,
    bidder: Principal,
    amount: int,
    idempotency_key: str | None,
) -> _TxOutcome:
    async with session.begin():
        lock_started = time.perf_counter()
        auction = (
            await session.execute(
                select(Auction).where(Auction.id == auction_id).with_for_update(of=Auction)
            )
        ).scalar_one_or_none()
        metrics.BID_LOCK_WAIT.observe(time.perf_counter() - lock_started)
        if auction is None or auction.status == "DRAFT":
            raise NotFound("Auction not found")

        now = await clock.now(session)  # after the lock: never stale

        if idempotency_key:
            replay = await _find_replay(session, bidder.id, idempotency_key)
            if replay is not None:
                if replay.auction_id != auction_id or replay.amount != amount:
                    raise IdempotencyConflict(
                        "Idempotency-Key was already used for a different bid"
                    )
                viewer = presenters.viewer_state(auction, bidder.id, replay.amount)
                return _TxOutcome(
                    BidAccepted(
                        bid=presenters.to_bid_out(replay),
                        auction=presenters.to_auction_out(auction, now, viewer),
                    ),
                    events=[],
                    replayed=True,
                )

        ctx = _context(auction)
        validate_bid(
            ctx,
            bidder_id=bidder.id,
            bidder_is_admin=bidder.is_admin,
            amount=amount,
            now=now,
            max_bid_amount=settings.max_bid_amount,
        )

        new_end = compute_new_end(ctx, now)
        previous_leader = auction.leader_id
        previous_amount = auction.current_price if auction.bid_count > 0 else None

        bid = Bid(
            auction_id=auction.id,
            bidder_id=bidder.id,
            amount=amount,
            idempotency_key=idempotency_key,
            created_at=now,
        )
        session.add(bid)
        auction.current_price = amount
        auction.leader_id = bidder.id
        auction.bid_count += 1
        auction.version += 1
        if new_end is not None:
            auction.end_at = new_end
            metrics.SNIPE_EXTENSIONS.inc()
        session.add(
            AuctionEvent(
                auction_id=auction.id,
                type="bid_placed",
                actor_id=bidder.id,
                payload={
                    "amount": amount,
                    "version": auction.version,
                    "bid_count": auction.bid_count,
                },
                created_at=now,
            )
        )
        if new_end is not None:
            session.add(
                AuctionEvent(
                    auction_id=auction.id,
                    type="extended",
                    actor_id=None,
                    payload={"new_end_at": new_end.isoformat(), "trigger_amount": amount},
                    created_at=now,
                )
            )
        try:
            await session.flush()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from exc

    log.info(
        "bid_accepted",
        amount=amount,
        version=auction.version,
        bid_count=auction.bid_count,
        extended=new_end is not None,
    )
    viewer = presenters.viewer_state(auction, bidder.id, amount)
    response = BidAccepted(
        bid=presenters.to_bid_out(bid),
        auction=presenters.to_auction_out(auction, now, viewer),
        extended=new_end is not None,
    )
    events = _build_events(
        auction,
        bid,
        now,
        extended=new_end is not None,
        previous_leader=previous_leader,
        previous_amount=previous_amount,
    )
    return _TxOutcome(response, events)


def _build_events(
    auction: Auction,
    bid: Bid,
    now: datetime,
    *,
    extended: bool,
    previous_leader: uuid.UUID | None,
    previous_amount: int | None,
) -> list[tuple[str, dict[str, Any]]]:
    public = build_event(
        "bid_placed",
        auction=auction,
        server_time=now,
        data={
            "current_price": auction.current_price,
            "min_next_bid": presenters.next_bid_floor(auction),
            "bid_count": auction.bid_count,
            "end_at": auction.end_at,
            "reserve_met": reserve_met(
                bid_count=auction.bid_count,
                current_price=auction.current_price,
                reserve_price=auction.reserve_price,
            ),
            "leader_alias": bidder_alias(auction.id, bid.bidder_id),
            "extended": extended,
            "bid": presenters.to_bid_out(bid).model_dump(mode="json"),
        },
    )
    items = [(room_auction(auction.id), public), (ROOM_LOBBY, public)]
    if previous_leader is not None and previous_leader != bid.bidder_id:
        items.append(
            (
                room_user(previous_leader),
                build_event(
                    "outbid",
                    auction=auction,
                    server_time=now,
                    data={
                        "title": bike_title(auction),
                        "your_amount": previous_amount,
                        "current_price": auction.current_price,
                        "min_next_bid": presenters.next_bid_floor(auction),
                    },
                ),
            )
        )
    return items


async def _find_replay(session: AsyncSession, bidder_id: uuid.UUID, key: str) -> Bid | None:
    return (
        await session.execute(
            select(Bid).where(Bid.bidder_id == bidder_id, Bid.idempotency_key == key)
        )
    ).scalar_one_or_none()


def _translate_integrity_error(exc: IntegrityError) -> DomainError:
    text = str(exc.orig)
    if "uq_bids_bidder_idem" in text:
        return IdempotencyConflict("Idempotency-Key was already used for a different bid")
    raise exc
