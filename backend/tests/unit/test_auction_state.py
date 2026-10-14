import itertools
import uuid

import pytest

from app.domain.auction_state import (
    TRANSITIONS,
    AuctionStatus,
    Outcome,
    assert_transition,
    can_transition,
    reserve_met,
    resolve_outcome,
)
from app.domain.errors import InvalidTransition

S = AuctionStatus
ALLOWED = {
    (S.DRAFT, S.SCHEDULED),
    (S.DRAFT, S.CANCELLED),
    (S.SCHEDULED, S.LIVE),
    (S.SCHEDULED, S.CANCELLED),
    (S.LIVE, S.ENDED),
    (S.LIVE, S.CANCELLED),
}


@pytest.mark.parametrize(("src", "dst"), list(itertools.product(S, S)))
def test_every_transition_pair_matches_the_documented_table(src: S, dst: S) -> None:
    assert can_transition(src, dst) is ((src, dst) in ALLOWED)


def test_terminal_states_have_no_exits() -> None:
    assert TRANSITIONS[S.ENDED] == frozenset()
    assert TRANSITIONS[S.CANCELLED] == frozenset()


def test_assert_transition_raises_with_context() -> None:
    with pytest.raises(InvalidTransition) as exc:
        assert_transition(S.ENDED, S.LIVE)
    assert exc.value.details == {"from_status": "ENDED", "to_status": "LIVE"}


def test_accepts_plain_strings() -> None:
    assert can_transition("LIVE", "ENDED")


LEADER = uuid.uuid4()


@pytest.mark.parametrize(
    ("bid_count", "price", "reserve", "leader", "expected"),
    [
        (0, 100, None, None, (Outcome.UNSOLD, None)),  # nobody bid
        (3, 500, None, LEADER, (Outcome.SOLD, LEADER)),  # no reserve: leader wins
        (3, 500, 500, LEADER, (Outcome.SOLD, LEADER)),  # reserve met exactly
        (3, 500, 400, LEADER, (Outcome.SOLD, LEADER)),  # reserve exceeded
        (3, 500, 501, LEADER, (Outcome.UNSOLD, None)),  # reserve missed by 1
    ],
)
def test_resolve_outcome(bid_count, price, reserve, leader, expected) -> None:  # type: ignore[no-untyped-def]
    assert (
        resolve_outcome(
            bid_count=bid_count, current_price=price, reserve_price=reserve, leader_id=leader
        )
        == expected
    )


@pytest.mark.parametrize(
    ("bid_count", "price", "reserve", "expected"),
    [
        (0, 100, None, None),
        (0, 100, 100, False),  # no bids yet: reserve cannot be "met" by the starting price
        (1, 99, 100, False),
        (1, 100, 100, True),
        (5, 250, 100, True),
    ],
)
def test_reserve_met(bid_count, price, reserve, expected) -> None:  # type: ignore[no-untyped-def]
    assert reserve_met(bid_count=bid_count, current_price=price, reserve_price=reserve) is expected
