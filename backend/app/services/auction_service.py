"""Admin commands on auctions: create, edit, schedule, cancel.

Every command takes the auction's row lock so it serialises cleanly with bids and the
scheduler, validates against the state machine, bumps ``version`` and writes an audit event.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.config import Settings
from app.core.security import Principal
from app.domain.auction_state import (
    EDITABLE_STATUSES,
    AuctionStatus,
    assert_transition,
)
from app.domain.errors import (
    AuctionNotEditable,
    BikeAlreadyListed,
    InvalidSchedule,
    NotFound,
)
from app.models.auction import Auction, AuctionEvent
from app.models.bike import Bike
from app.realtime.events import ROOM_LOBBY, build_event, room_auction
from app.realtime.publisher import Publisher
from app.schemas.auction import (
    MAX_AUCTION_DAYS,
    MIN_AUCTION_SECONDS,
    AuctionCreate,
    AuctionUpdate,
)
from app.services import presenters

log = structlog.stdlib.get_logger("app.auctions")

# Tolerate small client/server clock differences when an admin picks "start now".
START_GRACE = timedelta(seconds=30)


async def _lock(session: AsyncSession, auction_id: uuid.UUID) -> Auction:
    auction = (
        await session.execute(
            select(Auction).where(Auction.id == auction_id).with_for_update(of=Auction)
        )
    ).scalar_one_or_none()
    if auction is None:
        raise NotFound("Auction not found")
    return auction


def _validate_window(start_at: datetime, end_at: datetime, now: datetime) -> None:
    if end_at <= now:
        raise InvalidSchedule("End time must be in the future")
    if start_at < now - START_GRACE:
        raise InvalidSchedule("Start time is in the past")
    seconds = (end_at - start_at).total_seconds()
    if seconds < MIN_AUCTION_SECONDS:
        raise InvalidSchedule(f"Auction must run for at least {MIN_AUCTION_SECONDS} seconds")
    if seconds > MAX_AUCTION_DAYS * 86_400:
        raise InvalidSchedule(f"Auction cannot run longer than {MAX_AUCTION_DAYS} days")


def _event(
    auction: Auction, type_: str, actor: Principal | None, now: datetime, **payload: Any
) -> AuctionEvent:
    return AuctionEvent(
        auction_id=auction.id,
        type=type_,
        actor_id=actor.id if actor else None,
        payload=payload,
        created_at=now,
    )


def _broadcast(auction: Auction, type_: str, now: datetime) -> list[tuple[str, dict[str, Any]]]:
    """Public snapshot for list/detail pages. DRAFTs are invisible, so they emit nothing."""
    if auction.status == AuctionStatus.DRAFT:
        return []
    snapshot = presenters.to_auction_out(auction, now).model_dump(mode="json", exclude={"viewer"})
    event = build_event(type_, auction=auction, server_time=now, data=snapshot)
    return [(room_auction(auction.id), event), (ROOM_LOBBY, event)]


async def create_auction(
    session: AsyncSession,
    *,
    clock: Clock,
    settings: Settings,
    publisher: Publisher,
    admin: Principal,
    data: AuctionCreate,
) -> Auction:
    async with session.begin():
        now = await clock.now(session)
        bike = await session.get(Bike, data.bike_id)
        if bike is None:
            raise NotFound("Bike not found")
        _validate_window(data.start_at, data.end_at, now)

        auction = Auction(
            bike_id=bike.id,
            bike=bike,
            status=AuctionStatus.DRAFT,
            start_at=data.start_at,
            end_at=data.end_at,
            original_end_at=data.end_at,
            starting_price=data.starting_price,
            reserve_price=data.reserve_price,
            min_increment=data.min_increment,
            snipe_window_s=_coalesce(data.snipe_window_s, settings.default_snipe_window_s),
            snipe_extension_s=_coalesce(data.snipe_extension_s, settings.default_snipe_extension_s),
            max_extension_s=_coalesce(data.max_extension_s, settings.default_max_extension_s),
            current_price=data.starting_price,
            created_by=admin.id,
        )
        session.add(auction)
        try:
            await session.flush()
        except IntegrityError as exc:
            if "uq_auctions_open_bike" in str(exc.orig):
                raise BikeAlreadyListed("This bike already has an open auction") from exc
            raise
        session.add(_event(auction, "created", admin, now))
        if data.schedule:
            assert_transition(auction.status, AuctionStatus.SCHEDULED)
            auction.status = AuctionStatus.SCHEDULED
            auction.version += 1
            session.add(_event(auction, "scheduled", admin, now))
        events = _broadcast(auction, "auction_updated", now)
    log.info("auction_created", auction_id=str(auction.id), status=auction.status, by=str(admin.id))
    await publisher.publish_many(events)
    return auction


def _coalesce(value: int | None, default: int) -> int:
    return default if value is None else value


async def update_auction(
    session: AsyncSession,
    *,
    clock: Clock,
    publisher: Publisher,
    admin: Principal,
    auction_id: uuid.UUID,
    data: AuctionUpdate,
) -> Auction:
    async with session.begin():
        now = await clock.now(session)
        auction = await _lock(session, auction_id)
        if auction.status not in EDITABLE_STATUSES:
            raise AuctionNotEditable(
                "Only DRAFT or SCHEDULED auctions can be edited", status=auction.status
            )

        changes = data.model_dump(exclude_unset=True, exclude={"clear_reserve"})
        for field, value in changes.items():
            if value is not None:
                setattr(auction, field, value)
        if data.clear_reserve:
            auction.reserve_price = None
        if auction.reserve_price is not None and auction.reserve_price < auction.starting_price:
            raise InvalidSchedule("Reserve price cannot be below the starting price")

        _validate_window(auction.start_at, auction.end_at, now)
        # No bids can exist before the auction goes live, so price state simply follows the setup.
        auction.current_price = auction.starting_price
        auction.original_end_at = auction.end_at
        auction.version += 1
        session.add(_event(auction, "updated", admin, now, fields=sorted(changes)))
        events = _broadcast(auction, "auction_updated", now)
    log.info(
        "auction_updated", auction_id=str(auction.id), fields=sorted(changes), by=str(admin.id)
    )
    await publisher.publish_many(events)
    return auction


async def schedule_auction(
    session: AsyncSession,
    *,
    clock: Clock,
    publisher: Publisher,
    admin: Principal,
    auction_id: uuid.UUID,
) -> Auction:
    async with session.begin():
        now = await clock.now(session)
        auction = await _lock(session, auction_id)
        assert_transition(auction.status, AuctionStatus.SCHEDULED)
        _validate_window(auction.start_at, auction.end_at, now)
        auction.status = AuctionStatus.SCHEDULED
        auction.version += 1
        session.add(_event(auction, "scheduled", admin, now))
        events = _broadcast(auction, "auction_updated", now)
    log.info("auction_scheduled", auction_id=str(auction.id), by=str(admin.id))
    await publisher.publish_many(events)
    return auction


async def cancel_auction(
    session: AsyncSession,
    *,
    clock: Clock,
    publisher: Publisher,
    admin: Principal,
    auction_id: uuid.UUID,
    reason: str,
) -> Auction:
    async with session.begin():
        now = await clock.now(session)
        auction = await _lock(session, auction_id)
        was_visible = auction.status != AuctionStatus.DRAFT
        assert_transition(auction.status, AuctionStatus.CANCELLED)
        auction.status = AuctionStatus.CANCELLED
        auction.cancel_reason = reason
        auction.ended_at = now
        auction.version += 1
        session.add(_event(auction, "cancelled", admin, now, reason=reason))
        events = _cancel_events(auction, now) if was_visible else []
    log.info("auction_cancelled", auction_id=str(auction.id), reason=reason, by=str(admin.id))
    await publisher.publish_many(events)
    return auction


def _cancel_events(auction: Auction, now: datetime) -> list[tuple[str, dict[str, Any]]]:
    event = build_event(
        "auction_cancelled",
        auction=auction,
        server_time=now,
        data={"status": auction.status, "reason": auction.cancel_reason},
    )
    return [(room_auction(auction.id), event), (ROOM_LOBBY, event)]
