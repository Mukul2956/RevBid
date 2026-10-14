import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.bidding_rules import BidContext, compute_new_end, min_next_bid, validate_bid
from app.domain.errors import (
    AdminCannotBid,
    AlreadyHighestBidder,
    AuctionEnded,
    AuctionNotLive,
    BidTooHigh,
    BidTooLow,
)

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
ME, RIVAL = uuid.uuid4(), uuid.uuid4()
MAX_BID = 10**9


def ctx(**overrides: object) -> BidContext:
    base: dict[str, object] = {
        "status": "LIVE",
        "end_at": NOW + timedelta(minutes=30),
        "original_end_at": NOW + timedelta(minutes=30),
        "starting_price": 1_000,
        "current_price": 1_000,
        "min_increment": 100,
        "bid_count": 0,
        "leader_id": None,
        "snipe_window_s": 120,
        "snipe_extension_s": 120,
        "max_extension_s": 1_800,
    }
    base.update(overrides)
    return BidContext(**base)  # type: ignore[arg-type]


def check(
    c: BidContext, amount: int, *, bidder: uuid.UUID = ME, admin: bool = False, now: datetime = NOW
) -> None:
    validate_bid(
        c, bidder_id=bidder, bidder_is_admin=admin, amount=amount, now=now, max_bid_amount=MAX_BID
    )


class TestMinNextBid:
    def test_first_bid_may_equal_starting_price(self) -> None:
        assert (
            min_next_bid(starting_price=1_000, current_price=1_000, min_increment=100, bid_count=0)
            == 1_000
        )

    def test_later_bids_must_beat_lead_by_increment(self) -> None:
        assert (
            min_next_bid(starting_price=1_000, current_price=1_500, min_increment=100, bid_count=2)
            == 1_600
        )


class TestValidateBid:
    def test_accepts_first_bid_at_starting_price(self) -> None:
        check(ctx(), 1_000)

    def test_rejects_below_starting_price(self) -> None:
        with pytest.raises(BidTooLow) as exc:
            check(ctx(), 999)
        assert exc.value.details["min_next_bid"] == 1_000

    def test_requires_full_increment_over_current(self) -> None:
        c = ctx(current_price=1_500, bid_count=1, leader_id=RIVAL)
        check(c, 1_600)
        with pytest.raises(BidTooLow) as exc:
            check(c, 1_599)
        assert exc.value.details["min_next_bid"] == 1_600

    def test_leader_cannot_outbid_themselves(self) -> None:
        with pytest.raises(AlreadyHighestBidder):
            check(ctx(current_price=1_500, bid_count=1, leader_id=ME), 9_999)

    def test_admin_cannot_bid(self) -> None:
        with pytest.raises(AdminCannotBid):
            check(ctx(), 1_000, admin=True)

    @pytest.mark.parametrize("status", ["DRAFT", "SCHEDULED"])
    def test_not_live_yet(self, status: str) -> None:
        with pytest.raises(AuctionNotLive):
            check(ctx(status=status), 1_000)

    @pytest.mark.parametrize("status", ["ENDED", "CANCELLED"])
    def test_finished_auctions(self, status: str) -> None:
        with pytest.raises(AuctionEnded):
            check(ctx(status=status), 1_000)

    def test_clock_wins_over_status_when_scheduler_lags(self) -> None:
        """Status still says LIVE, but end_at has passed: the bid must be refused."""
        c = ctx(end_at=NOW)
        with pytest.raises(AuctionEnded):
            check(c, 1_000, now=NOW)  # exactly at end_at is already over
        check(c, 1_000, now=NOW - timedelta(milliseconds=1))  # a millisecond earlier is fine

    def test_fat_finger_cap(self) -> None:
        with pytest.raises(BidTooHigh):
            check(ctx(), MAX_BID + 1)
        check(ctx(), MAX_BID)

    def test_already_highest_reported_before_amount_problems(self) -> None:
        with pytest.raises(AlreadyHighestBidder):
            check(ctx(current_price=1_500, bid_count=1, leader_id=ME), 1)


class TestAntiSniping:
    def test_no_extension_outside_window(self) -> None:
        assert compute_new_end(ctx(end_at=NOW + timedelta(seconds=121)), NOW) is None

    def test_extends_inside_window(self) -> None:
        c = ctx(end_at=NOW + timedelta(seconds=30))
        assert compute_new_end(c, NOW) == NOW + timedelta(seconds=120)

    def test_window_boundary_is_inclusive(self) -> None:
        c = ctx(end_at=NOW + timedelta(seconds=120), snipe_extension_s=180)
        assert compute_new_end(c, NOW) == NOW + timedelta(seconds=180)
        just_outside = ctx(end_at=NOW + timedelta(seconds=121), snipe_extension_s=180)
        assert compute_new_end(just_outside, NOW) is None

    def test_never_shortens_the_auction(self) -> None:
        # end is 60s away, extension would be 30s from now -> candidate earlier than end_at
        c = ctx(end_at=NOW + timedelta(seconds=60), snipe_extension_s=30)
        assert compute_new_end(c, NOW) is None

    def test_total_extension_is_capped(self) -> None:
        original = NOW + timedelta(seconds=10)
        c = ctx(
            end_at=NOW + timedelta(seconds=10),
            original_end_at=original - timedelta(seconds=1700),
            max_extension_s=1_800,
        )
        # cap = original-1700s + 1800s = original + 100s
        new_end = compute_new_end(c, NOW)
        assert new_end == c.original_end_at + timedelta(seconds=1_800)
        assert new_end is not None and new_end < NOW + timedelta(seconds=120)

    def test_cap_reached_means_no_further_extension(self) -> None:
        end = NOW + timedelta(seconds=10)
        c = ctx(end_at=end, original_end_at=end - timedelta(seconds=1_800), max_extension_s=1_800)
        assert compute_new_end(c, NOW) is None

    @pytest.mark.parametrize(("window", "ext"), [(0, 120), (120, 0)])
    def test_disabled_when_window_or_extension_is_zero(self, window: int, ext: int) -> None:
        c = ctx(end_at=NOW + timedelta(seconds=5), snipe_window_s=window, snipe_extension_s=ext)
        assert compute_new_end(c, NOW) is None
