"""Read-side queries for auctions and bids (no writes, no locks)."""

import uuid
from typing import Literal

from sqlalchemy import Select, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import contains_eager

from app.core.clock import Clock
from app.domain.auction_state import AuctionStatus
from app.domain.errors import NotFound
from app.models.auction import Auction, Bid
from app.models.bike import Bike
from app.schemas.auction import AdminAuctionOut, AuctionOut, BidOut, MyBidOut
from app.schemas.common import Page
from app.services import presenters
from app.services.pagination import decode_cursor, encode_cursor
from app.services.sqlutil import escape_like

SortKey = Literal["ending_soon", "newest", "price_low", "price_high"]

# sort key -> (column, ascending, is_datetime)
_SORTS = {
    "ending_soon": (Auction.end_at, True, True),
    "newest": (Auction.start_at, False, True),
    "price_low": (Auction.current_price, True, False),
    "price_high": (Auction.current_price, False, False),
}

PUBLIC_STATUSES = (AuctionStatus.SCHEDULED, AuctionStatus.LIVE, AuctionStatus.ENDED)


async def _my_highest_bids(
    session: AsyncSession, user_id: uuid.UUID | None, auction_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if user_id is None or not auction_ids:
        return {}
    rows = await session.execute(
        select(Bid.auction_id, func.max(Bid.amount))
        .where(Bid.bidder_id == user_id, Bid.auction_id.in_(auction_ids))
        .group_by(Bid.auction_id)
    )
    return dict(rows.all())


async def get_public_auction(
    session: AsyncSession, clock: Clock, auction_id: uuid.UUID, viewer_id: uuid.UUID | None
) -> AuctionOut:
    auction = await session.get(Auction, auction_id)
    if auction is None or auction.status == AuctionStatus.DRAFT:
        raise NotFound("Auction not found")
    mine = await _my_highest_bids(session, viewer_id, [auction.id])
    viewer = presenters.viewer_state(auction, viewer_id, mine.get(auction.id))
    return presenters.to_auction_out(auction, clock.wall_time(), viewer)


async def get_admin_auction(
    session: AsyncSession, clock: Clock, auction_id: uuid.UUID
) -> AdminAuctionOut:
    auction = await session.get(Auction, auction_id)
    if auction is None:
        raise NotFound("Auction not found")
    return presenters.to_admin_auction_out(auction, clock.wall_time())


def _filtered(
    statuses: list[str],
    q: str | None,
    make: str | None,
    year_min: int | None,
    year_max: int | None,
    price_min: int | None,
    price_max: int | None,
) -> Select[Auction]:
    stmt = (
        select(Auction)
        .join(Auction.bike)
        .options(contains_eager(Auction.bike))
        .where(Auction.status.in_(statuses))
    )
    if q:
        like = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(
            or_(
                Bike.make.ilike(like, escape="\\"),
                Bike.model.ilike(like, escape="\\"),
                Bike.variant.ilike(like, escape="\\"),
            )
        )
    if make:
        stmt = stmt.where(func.lower(Bike.make) == make.lower())
    if year_min is not None:
        stmt = stmt.where(Bike.year >= year_min)
    if year_max is not None:
        stmt = stmt.where(Bike.year <= year_max)
    if price_min is not None:
        stmt = stmt.where(Auction.current_price >= price_min)
    if price_max is not None:
        stmt = stmt.where(Auction.current_price <= price_max)
    return stmt


async def _page(
    session: AsyncSession,
    stmt: Select[Auction],
    sort: SortKey,
    cursor: str | None,
    limit: int,
) -> tuple[list[Auction], str | None]:
    column, ascending, is_dt = _SORTS[sort]
    if cursor:
        value, last_id = decode_cursor(cursor, as_datetime=is_dt)
        pair, bound = tuple_(column, Auction.id), tuple_(value, last_id)
        stmt = stmt.where(pair > bound if ascending else pair < bound)
    order = (column.asc(), Auction.id.asc()) if ascending else (column.desc(), Auction.id.desc())
    rows = list((await session.execute(stmt.order_by(*order).limit(limit + 1))).scalars().unique())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(getattr(last, column.key), last.id)
    return rows, next_cursor


async def list_public_auctions(
    session: AsyncSession,
    clock: Clock,
    *,
    viewer_id: uuid.UUID | None,
    statuses: list[str],
    q: str | None = None,
    make: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    sort: SortKey = "ending_soon",
    cursor: str | None = None,
    limit: int = 20,
) -> Page[AuctionOut]:
    allowed = [s for s in statuses if s in PUBLIC_STATUSES]
    stmt = _filtered(allowed, q, make, year_min, year_max, price_min, price_max)
    rows, next_cursor = await _page(session, stmt, sort, cursor, limit)
    mine = await _my_highest_bids(session, viewer_id, [a.id for a in rows])
    now = clock.wall_time()
    items = [
        presenters.to_auction_out(a, now, presenters.viewer_state(a, viewer_id, mine.get(a.id)))
        for a in rows
    ]
    return Page[AuctionOut](items=items, next_cursor=next_cursor)


async def list_admin_auctions(
    session: AsyncSession,
    clock: Clock,
    *,
    statuses: list[str],
    q: str | None = None,
    sort: SortKey = "newest",
    cursor: str | None = None,
    limit: int = 20,
) -> Page[AdminAuctionOut]:
    stmt = _filtered(statuses or [s.value for s in AuctionStatus], q, None, None, None, None, None)
    rows, next_cursor = await _page(session, stmt, sort, cursor, limit)
    now = clock.wall_time()
    return Page[AdminAuctionOut](
        items=[presenters.to_admin_auction_out(a, now) for a in rows], next_cursor=next_cursor
    )


async def list_makes(session: AsyncSession) -> list[str]:
    """Distinct makes among publicly visible auctions (feeds the filter dropdown)."""
    rows = await session.execute(
        select(Bike.make)
        .join(Auction, Auction.bike_id == Bike.id)
        .where(Auction.status.in_(PUBLIC_STATUSES))
        .distinct()
        .order_by(Bike.make)
    )
    return list(rows.scalars())


async def list_bids(
    session: AsyncSession, auction_id: uuid.UUID, *, cursor: str | None, limit: int
) -> Page[BidOut]:
    auction = await session.get(Auction, auction_id)
    if auction is None or auction.status == AuctionStatus.DRAFT:
        raise NotFound("Auction not found")
    # Amounts are unique and strictly increasing per auction, so amount *is* the keyset.
    stmt = (
        select(Bid).where(Bid.auction_id == auction_id).order_by(Bid.amount.desc()).limit(limit + 1)
    )
    if cursor:
        value, _ = decode_cursor(cursor, as_datetime=False)
        stmt = stmt.where(Bid.amount < value)
    rows = list((await session.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].amount, rows[-1].id)
    return Page[BidOut](items=[presenters.to_bid_out(b) for b in rows], next_cursor=next_cursor)


async def my_bids(
    session: AsyncSession, clock: Clock, user_id: uuid.UUID, *, limit: int = 100
) -> list[MyBidOut]:
    mine = (
        select(
            Bid.auction_id.label("auction_id"),
            func.max(Bid.amount).label("amount"),
            func.max(Bid.created_at).label("last_bid_at"),
        )
        .where(Bid.bidder_id == user_id)
        .group_by(Bid.auction_id)
        .subquery()
    )
    rows = (
        (
            await session.execute(
                select(Auction, mine.c.amount, mine.c.last_bid_at)
                .join(mine, mine.c.auction_id == Auction.id)
                .order_by(mine.c.last_bid_at.desc())
                .limit(limit)
            )
        )
        .unique()
        .all()
    )
    now = clock.wall_time()
    out: list[MyBidOut] = []
    for auction, amount, last_bid_at in rows:
        if auction.status == AuctionStatus.CANCELLED:
            result = "CANCELLED"
        elif auction.status == AuctionStatus.ENDED:
            result = "WON" if auction.winner_id == user_id else "LOST"
        else:
            result = "LEADING" if auction.leader_id == user_id else "OUTBID"
        out.append(
            MyBidOut(
                auction=presenters.to_auction_out(
                    auction, now, presenters.viewer_state(auction, user_id, amount)
                ),
                my_highest_bid=amount,
                last_bid_at=last_bid_at,
                result=result,
            )
        )
    return out
