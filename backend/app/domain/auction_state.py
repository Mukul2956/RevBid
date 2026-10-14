"""Auction lifecycle state machine. Pure: no I/O, no clock, no database.

::

    DRAFT ──► SCHEDULED ──► LIVE ──► ENDED (outcome SOLD | UNSOLD)
      │           │           │
      └───────────┴───────────┴────► CANCELLED   (admin action, reason recorded)

ENDED and CANCELLED are terminal.
"""

import uuid
from enum import StrEnum

from app.domain.errors import InvalidTransition


class AuctionStatus(StrEnum):
    DRAFT = "DRAFT"
    SCHEDULED = "SCHEDULED"
    LIVE = "LIVE"
    ENDED = "ENDED"
    CANCELLED = "CANCELLED"


class Outcome(StrEnum):
    SOLD = "SOLD"
    UNSOLD = "UNSOLD"


S = AuctionStatus
TRANSITIONS: dict[AuctionStatus, frozenset[AuctionStatus]] = {
    S.DRAFT: frozenset({S.SCHEDULED, S.CANCELLED}),
    S.SCHEDULED: frozenset({S.LIVE, S.CANCELLED}),
    S.LIVE: frozenset({S.ENDED, S.CANCELLED}),
    S.ENDED: frozenset(),
    S.CANCELLED: frozenset(),
}

EDITABLE_STATUSES = frozenset({S.DRAFT, S.SCHEDULED})
OPEN_STATUSES = frozenset({S.DRAFT, S.SCHEDULED, S.LIVE})


def can_transition(src: AuctionStatus | str, dst: AuctionStatus | str) -> bool:
    return AuctionStatus(dst) in TRANSITIONS[AuctionStatus(src)]


def assert_transition(src: AuctionStatus | str, dst: AuctionStatus | str) -> None:
    if not can_transition(src, dst):
        raise InvalidTransition(
            f"Cannot move an auction from {src} to {dst}", from_status=str(src), to_status=str(dst)
        )


def resolve_outcome(
    *,
    bid_count: int,
    current_price: int,
    reserve_price: int | None,
    leader_id: uuid.UUID | None,
) -> tuple[Outcome, uuid.UUID | None]:
    """Decide the result when an auction closes. The leading bidder wins iff the reserve is met."""
    if bid_count == 0 or leader_id is None:
        return Outcome.UNSOLD, None
    if reserve_price is not None and current_price < reserve_price:
        return Outcome.UNSOLD, None
    return Outcome.SOLD, leader_id


def reserve_met(*, bid_count: int, current_price: int, reserve_price: int | None) -> bool | None:
    """``None`` when the auction has no reserve; otherwise whether bidding has reached it."""
    if reserve_price is None:
        return None
    return bid_count > 0 and current_price >= reserve_price
