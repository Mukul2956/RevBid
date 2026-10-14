from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import FakeClock
from app.models.auction import Auction, AuctionEvent
from tests.factories import RUPEE, auth, make_auction, make_user
from tests.integration.test_bidding import bid


@pytest.fixture
def scheduler(app):  # type: ignore[no-untyped-def]
    return app.state.scheduler


async def reload(sessionmaker, auction_id):  # type: ignore[no-untyped-def]
    async with sessionmaker() as s:
        return await s.get(Auction, auction_id)


async def test_scheduled_auction_goes_live_exactly_when_start_arrives(
    scheduler, clock: FakeClock, sessionmaker, publisher
) -> None:
    auction = await make_auction(
        sessionmaker, now=clock.current, status="SCHEDULED", start_in=timedelta(minutes=10)
    )

    result = await scheduler.tick()
    assert result.promoted == 0
    assert (await reload(sessionmaker, auction.id)).status == "SCHEDULED"

    clock.advance(minutes=10)
    result = await scheduler.tick()

    assert result.promoted == 1
    row = await reload(sessionmaker, auction.id)
    assert row.status == "LIVE" and row.version == 1
    started = publisher.of_type("auction_started")
    assert {room for room, _ in started} == {f"auction:{auction.id}", "lobby"}


async def test_auction_with_winning_bid_ends_sold(
    client, scheduler, clock: FakeClock, sessionmaker, settings, publisher
) -> None:
    winner, loser = await make_user(sessionmaker), await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    await bid(client, auth(settings, loser), auction.id, auction.starting_price)
    r = await bid(
        client, auth(settings, winner), auction.id, auction.starting_price + auction.min_increment
    )
    final = r.json()["auction"]["current_price"]
    publisher.events.clear()

    clock.current = auction.end_at + timedelta(seconds=3)
    result = await scheduler.tick()

    assert result.closed == 1
    row = await reload(sessionmaker, auction.id)
    assert (row.status, row.outcome, row.winner_id) == ("ENDED", "SOLD", winner.id)
    assert row.current_price == final and row.ended_at == clock.current
    won = publisher.of_type("auction_won")
    assert [room for room, _ in won] == [f"user:{winner.id}"]
    ended = publisher.of_type("auction_ended")[0][1]["data"]
    assert ended["outcome"] == "SOLD" and ended["final_price"] == final
    assert str(winner.id) not in str(ended)  # public event carries only the alias


async def test_auction_without_bids_ends_unsold(scheduler, clock: FakeClock, sessionmaker) -> None:
    auction = await make_auction(sessionmaker, now=clock.current)
    clock.current = auction.end_at
    await scheduler.tick()
    row = await reload(sessionmaker, auction.id)
    assert (row.status, row.outcome, row.winner_id) == ("ENDED", "UNSOLD", None)


async def test_reserve_not_met_ends_unsold_even_with_bids(
    client, scheduler, clock: FakeClock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current, reserve_price=90_000 * RUPEE)
    await bid(client, auth(settings, user), auction.id, auction.starting_price)

    clock.current = auction.end_at + timedelta(seconds=1)
    await scheduler.tick()

    row = await reload(sessionmaker, auction.id)
    assert (row.status, row.outcome, row.winner_id) == ("ENDED", "UNSOLD", None)
    assert row.bid_count == 1  # bids are retained as history


async def test_reserve_met_ends_sold(
    client, scheduler, clock: FakeClock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current, reserve_price=60_000 * RUPEE)
    await bid(client, auth(settings, user), auction.id, 60_000 * RUPEE)
    clock.current = auction.end_at + timedelta(seconds=1)
    await scheduler.tick()
    assert (await reload(sessionmaker, auction.id)).outcome == "SOLD"


async def test_snipe_extension_postpones_the_close(
    client, scheduler, clock: FakeClock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    clock.current = auction.end_at - timedelta(seconds=20)
    await bid(client, auth(settings, user), auction.id, auction.starting_price)

    clock.current = auction.end_at + timedelta(seconds=5)  # past the *original* end
    assert (await scheduler.tick()).closed == 0
    assert (await reload(sessionmaker, auction.id)).status == "LIVE"

    clock.current = auction.end_at + timedelta(seconds=110)  # past the extended end
    assert (await scheduler.tick()).closed == 1


async def test_late_bid_after_close_is_rejected(
    client, scheduler, clock: FakeClock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    clock.current = auction.end_at + timedelta(seconds=1)
    await scheduler.tick()
    r = await bid(client, auth(settings, user), auction.id, auction.starting_price)
    assert r.status_code == 409 and r.json()["code"] == "AUCTION_ENDED"


async def test_tick_is_idempotent(scheduler, clock: FakeClock, sessionmaker, publisher) -> None:
    auction = await make_auction(sessionmaker, now=clock.current)
    clock.current = auction.end_at + timedelta(seconds=1)
    assert (await scheduler.tick()).closed == 1
    publisher.events.clear()
    assert (await scheduler.tick()).closed == 0
    assert publisher.events == []
    async with sessionmaker() as s:
        kinds = (
            (await s.execute(select(AuctionEvent.type).where(AuctionEvent.type == "ended")))
            .scalars()
            .all()
        )
        assert len(kinds) == 1


async def test_restart_after_downtime_catches_up_in_one_tick(
    scheduler, clock: FakeClock, sessionmaker
) -> None:
    """Simulates a sleeping free-tier instance: everything overdue is settled on the first tick."""
    should_start = await make_auction(
        sessionmaker,
        now=clock.current,
        status="SCHEDULED",
        start_in=timedelta(minutes=1),
        duration=timedelta(minutes=5),
    )
    should_end = await make_auction(sessionmaker, now=clock.current)
    clock.advance(hours=3)

    result = await scheduler.tick()

    assert (result.promoted, result.closed) == (1, 2)  # the late starter is started, then closed
    assert (await reload(sessionmaker, should_end.id)).status == "ENDED"
    assert (await reload(sessionmaker, should_start.id)).status == "ENDED"


async def test_many_due_auctions_are_all_closed(scheduler, clock: FakeClock, sessionmaker) -> None:
    ids = [(await make_auction(sessionmaker, now=clock.current)).id for _ in range(7)]
    clock.advance(hours=2)
    assert (await scheduler.tick()).closed == 7
    for auction_id in ids:
        assert (await reload(sessionmaker, auction_id)).status == "ENDED"
