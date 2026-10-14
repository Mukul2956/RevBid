import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.schemas.bike import BikeOut
from app.schemas.common import ApiModel, AwareDatetime

MIN_AUCTION_SECONDS = 60
MAX_AUCTION_DAYS = 30


class AuctionCreate(ApiModel):
    bike_id: uuid.UUID
    start_at: AwareDatetime
    end_at: AwareDatetime
    starting_price: int = Field(gt=0, le=10_000_000_000, description="Paise")
    reserve_price: int | None = Field(default=None, gt=0, le=10_000_000_000)
    min_increment: int = Field(gt=0, le=1_000_000_000)
    snipe_window_s: int | None = Field(default=None, ge=0, le=3600)
    snipe_extension_s: int | None = Field(default=None, ge=0, le=3600)
    max_extension_s: int | None = Field(default=None, ge=0, le=86_400)
    schedule: bool = Field(default=False, description="Move straight to SCHEDULED instead of DRAFT")

    @model_validator(mode="after")
    def _check(self) -> "AuctionCreate":
        seconds = (self.end_at - self.start_at).total_seconds()
        if seconds < MIN_AUCTION_SECONDS:
            raise ValueError(f"Auction must run for at least {MIN_AUCTION_SECONDS} seconds")
        if seconds > MAX_AUCTION_DAYS * 86_400:
            raise ValueError(f"Auction cannot run longer than {MAX_AUCTION_DAYS} days")
        if self.reserve_price is not None and self.reserve_price < self.starting_price:
            raise ValueError("Reserve price cannot be below the starting price")
        return self


class AuctionUpdate(ApiModel):
    """Editable only while DRAFT or SCHEDULED."""

    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    starting_price: int | None = Field(default=None, gt=0, le=10_000_000_000)
    reserve_price: int | None = Field(default=None, gt=0, le=10_000_000_000)
    clear_reserve: bool = False
    min_increment: int | None = Field(default=None, gt=0, le=1_000_000_000)
    snipe_window_s: int | None = Field(default=None, ge=0, le=3600)
    snipe_extension_s: int | None = Field(default=None, ge=0, le=3600)
    max_extension_s: int | None = Field(default=None, ge=0, le=86_400)


class CancelIn(ApiModel):
    reason: str = Field(min_length=3, max_length=255)


class Viewer(ApiModel):
    """The calling user's relationship to an auction (omitted for anonymous callers)."""

    alias: str
    is_leading: bool
    my_highest_bid: int | None


class AuctionOut(ApiModel):
    """Public view. Never contains the reserve price or any bidder identity."""

    id: uuid.UUID
    status: str
    outcome: str | None
    cancel_reason: str | None
    start_at: datetime
    end_at: datetime
    starting_price: int
    current_price: int
    min_increment: int
    min_next_bid: int
    bid_count: int
    has_reserve: bool
    reserve_met: bool | None
    leader_alias: str | None
    winner_alias: str | None
    version: int
    server_time: datetime
    bike: BikeOut
    viewer: Viewer | None = None


class AdminAuctionOut(AuctionOut):
    reserve_price: int | None
    leader_id: uuid.UUID | None
    winner_id: uuid.UUID | None
    original_end_at: datetime
    snipe_window_s: int
    snipe_extension_s: int
    max_extension_s: int
    created_at: datetime


class BidIn(ApiModel):
    amount: int = Field(gt=0, description="Paise (1 INR = 100 paise)")


class BidOut(ApiModel):
    id: uuid.UUID
    amount: int
    created_at: datetime
    bidder_alias: str


class BidAccepted(ApiModel):
    bid: BidOut
    auction: AuctionOut
    extended: bool = False


MyBidResult = Literal["LEADING", "OUTBID", "WON", "LOST", "CANCELLED"]


class MyBidOut(ApiModel):
    auction: AuctionOut
    my_highest_bid: int
    last_bid_at: datetime
    result: MyBidResult
