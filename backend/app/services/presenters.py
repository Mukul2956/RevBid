"""Turn ORM rows into API views. The only place that decides what each audience may see."""

import uuid
from datetime import datetime

from app.domain.auction_state import reserve_met
from app.domain.bidding_rules import min_next_bid
from app.domain.masking import bidder_alias
from app.models.auction import Auction, Bid
from app.schemas.auction import AdminAuctionOut, AuctionOut, BidOut, Viewer
from app.schemas.bike import BikeOut


def _alias(auction: Auction, user_id: uuid.UUID | None) -> str | None:
    return bidder_alias(auction.id, user_id) if user_id else None


def viewer_state(
    auction: Auction, viewer_id: uuid.UUID | None, my_highest_bid: int | None
) -> Viewer | None:
    if viewer_id is None:
        return None
    return Viewer(
        alias=bidder_alias(auction.id, viewer_id),
        is_leading=auction.leader_id == viewer_id,
        my_highest_bid=my_highest_bid,
    )


def next_bid_floor(auction: Auction) -> int:
    return min_next_bid(
        starting_price=auction.starting_price,
        current_price=auction.current_price,
        min_increment=auction.min_increment,
        bid_count=auction.bid_count,
    )


def _public_fields(auction: Auction, now: datetime) -> dict[str, object]:
    return {
        "id": auction.id,
        "status": auction.status,
        "outcome": auction.outcome,
        "cancel_reason": auction.cancel_reason,
        "start_at": auction.start_at,
        "end_at": auction.end_at,
        "starting_price": auction.starting_price,
        "current_price": auction.current_price,
        "min_increment": auction.min_increment,
        "min_next_bid": next_bid_floor(auction),
        "bid_count": auction.bid_count,
        "has_reserve": auction.reserve_price is not None,
        "reserve_met": reserve_met(
            bid_count=auction.bid_count,
            current_price=auction.current_price,
            reserve_price=auction.reserve_price,
        ),
        "leader_alias": _alias(auction, auction.leader_id),
        "winner_alias": _alias(auction, auction.winner_id),
        "version": auction.version,
        "server_time": now,
        "bike": BikeOut.model_validate(auction.bike),
    }


def to_auction_out(auction: Auction, now: datetime, viewer: Viewer | None = None) -> AuctionOut:
    return AuctionOut(**_public_fields(auction, now), viewer=viewer)


def to_admin_auction_out(auction: Auction, now: datetime) -> AdminAuctionOut:
    return AdminAuctionOut(
        **_public_fields(auction, now),
        reserve_price=auction.reserve_price,
        leader_id=auction.leader_id,
        winner_id=auction.winner_id,
        original_end_at=auction.original_end_at,
        snipe_window_s=auction.snipe_window_s,
        snipe_extension_s=auction.snipe_extension_s,
        max_extension_s=auction.max_extension_s,
        created_at=auction.created_at,
    )


def to_bid_out(bid: Bid) -> BidOut:
    return BidOut(
        id=bid.id,
        amount=bid.amount,
        created_at=bid.created_at,
        bidder_alias=bidder_alias(bid.auction_id, bid.bidder_id),
    )
