"""Bid validation and anti-sniping arithmetic. Pure functions over a snapshot of the auction.

The snapshot is read while holding the auction's row lock, so these rules can assume nothing
changes underneath them for the duration of the check.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.auction_state import AuctionStatus
from app.domain.errors import (
    AdminCannotBid,
    AlreadyHighestBidder,
    AuctionEnded,
    AuctionNotLive,
    BidTooHigh,
    BidTooLow,
)


@dataclass(frozen=True)
class BidContext:
    status: str
    end_at: datetime
    original_end_at: datetime
    starting_price: int
    current_price: int
    min_increment: int
    bid_count: int
    leader_id: uuid.UUID | None
    snipe_window_s: int
    snipe_extension_s: int
    max_extension_s: int


def min_next_bid(
    *, starting_price: int, current_price: int, min_increment: int, bid_count: int
) -> int:
    """First bid may equal the starting price; later bids must beat the lead by the increment."""
    if bid_count == 0:
        return starting_price
    return current_price + min_increment


def validate_bid(
    ctx: BidContext,
    *,
    bidder_id: uuid.UUID,
    bidder_is_admin: bool,
    amount: int,
    now: datetime,
    max_bid_amount: int,
) -> None:
    """Raise a ``DomainError`` subclass if the bid must be rejected."""
    if bidder_is_admin:
        raise AdminCannotBid("Administrators cannot place bids")

    if ctx.status in (AuctionStatus.ENDED, AuctionStatus.CANCELLED):
        raise AuctionEnded("This auction has ended", auction_status=ctx.status)
    if ctx.status != AuctionStatus.LIVE:
        raise AuctionNotLive("This auction is not live yet", auction_status=ctx.status)
    # The clock, not the scheduler, decides when bidding stops: a lagging worker must never
    # let a late bid through.
    if now >= ctx.end_at:
        raise AuctionEnded("This auction has ended", auction_status=ctx.status)

    if ctx.leader_id == bidder_id:
        raise AlreadyHighestBidder("You are already the highest bidder")

    if amount > max_bid_amount:
        raise BidTooHigh("Bid exceeds the maximum allowed amount", max_bid=max_bid_amount)

    floor = min_next_bid(
        starting_price=ctx.starting_price,
        current_price=ctx.current_price,
        min_increment=ctx.min_increment,
        bid_count=ctx.bid_count,
    )
    if amount < floor:
        raise BidTooLow(
            "Bid is below the minimum next bid",
            min_next_bid=floor,
            current_price=ctx.current_price,
        )


def compute_new_end(ctx: BidContext, now: datetime) -> datetime | None:
    """Anti-sniping: a bid inside the final window pushes the end time out.

    Returns the new ``end_at`` or ``None`` if no extension applies. Total extension is capped
    at ``max_extension_s`` beyond the original end so an auction can't be dragged on forever.
    """
    if ctx.snipe_window_s <= 0 or ctx.snipe_extension_s <= 0:
        return None
    if (ctx.end_at - now).total_seconds() > ctx.snipe_window_s:
        return None
    cap = ctx.original_end_at + timedelta(seconds=ctx.max_extension_s)
    candidate = min(now + timedelta(seconds=ctx.snipe_extension_s), cap)
    return candidate if candidate > ctx.end_at else None
