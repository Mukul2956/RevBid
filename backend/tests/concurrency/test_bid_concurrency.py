"""Race-condition tests for the bid path and the close path.

These run against real PostgreSQL with genuinely concurrent requests, then assert *invariants*
rather than a single expected interleaving, because the interleaving is non-deterministic and
the invariants are what must always hold:

  I1  accepted bid amounts are strictly increasing in lock order, each >= previous + increment
  I2  auction.current_price == highest accepted amount; leader == bidder of that bid
  I3  auction.bid_count == number of bid rows == number of 201 responses
  I4  no duplicate amounts; the set of 201 amounts == the set of persisted amounts
  I5  version advances by exactly one per accepted bid
  I6  nothing is committed after the auction is closed; the winner is the final leader
"""

import asyncio
import itertools
import random
import uuid
from collections import Counter

import httpx
import pytest
from sqlalchemy import select, text

from app.models.auction import Auction, AuctionEvent, Bid
from tests.factories import auth, make_auction, make_user

pytestmark = pytest.mark.concurrency


async def post_bid(client, headers, auction_id, amount, key=None):  # type: ignore[no-untyped-def]
    h = {**headers, **({"Idempotency-Key": key} if key else {})}
    return await client.post(
        f"/api/v1/auctions/{auction_id}/bids", json={"amount": amount}, headers=h
    )


async def assert_consistent(
    sessionmaker, auction_id, accepted: list[int], *, start_version: int = 0
):  # type: ignore[no-untyped-def]
    async with sessionmaker() as s:
        auction = await s.get(Auction, auction_id)
        bids = (await s.execute(select(Bid).where(Bid.auction_id == auction_id))).scalars().all()
        events = (
            (
                await s.execute(
                    select(AuctionEvent)
                    .where(AuctionEvent.auction_id == auction_id, AuctionEvent.type == "bid_placed")
                    .order_by(AuctionEvent.id)
                )
            )
            .scalars()
            .all()
        )

    amounts = [b.amount for b in bids]
    sequence = [e.payload["amount"] for e in events]  # lock order == event id order

    assert len(set(amounts)) == len(amounts), "I4: duplicate amounts persisted"
    assert sorted(amounts) == sorted(accepted), (
        "I4: persisted amounts differ from accepted responses"
    )
    assert auction.bid_count == len(bids) == len(accepted), "I3"  # type: ignore[union-attr]
    assert auction.version == start_version + len(accepted), "I5"  # type: ignore[union-attr]
    if not bids:
        return
    top = max(bids, key=lambda b: b.amount)
    assert auction.current_price == top.amount, "I2: price"  # type: ignore[union-attr]
    assert auction.leader_id == top.bidder_id, "I2: leader"  # type: ignore[union-attr]
    assert sequence == sorted(sequence), "I1: amounts not increasing in lock order"
    assert sequence[0] >= auction.starting_price  # type: ignore[union-attr]
    for prev, nxt in itertools.pairwise(sequence):
        assert nxt - prev >= auction.min_increment, "I1: increment violated"  # type: ignore[union-attr]


async def test_identical_simultaneous_bids_exactly_one_wins(
    client, clock, sessionmaker, settings
) -> None:
    users = [await make_user(sessionmaker) for _ in range(30)]
    auction = await make_auction(sessionmaker, now=clock.current)

    responses = await asyncio.gather(
        *(post_bid(client, auth(settings, u), auction.id, auction.starting_price) for u in users)
    )

    codes = Counter(r.status_code for r in responses)
    assert codes == {201: 1, 409: 29}
    assert {r.json()["code"] for r in responses if r.status_code == 409} == {"BID_TOO_LOW"}
    await assert_consistent(sessionmaker, auction.id, [auction.starting_price])


async def test_bid_storm_preserves_invariants(client, clock, sessionmaker, settings) -> None:
    rng = random.Random(1234)
    users = [await make_user(sessionmaker) for _ in range(40)]
    auction = await make_auction(sessionmaker, now=clock.current)
    inc = auction.min_increment

    attempts = [
        (u, auction.starting_price + rng.randint(0, 60) * inc) for u in users for _ in range(3)
    ]
    rng.shuffle(attempts)
    responses = await asyncio.gather(
        *(post_bid(client, auth(settings, u), auction.id, amount) for u, amount in attempts)
    )

    accepted = [r.json()["bid"]["amount"] for r in responses if r.status_code == 201]
    rejected = {r.json()["code"] for r in responses if r.status_code != 201}
    assert accepted, "a storm of 120 attempts must land at least one bid"
    assert rejected <= {"BID_TOO_LOW", "ALREADY_HIGHEST"}, rejected
    await assert_consistent(sessionmaker, auction.id, accepted)


async def test_independent_auctions_do_not_interfere(client, clock, sessionmaker, settings) -> None:
    rng = random.Random(99)
    auctions = [await make_auction(sessionmaker, now=clock.current) for _ in range(5)]
    users = [await make_user(sessionmaker) for _ in range(12)]
    jobs = [
        (a, u, a.starting_price + rng.randint(0, 25) * a.min_increment)
        for a in auctions
        for u in users
    ]
    rng.shuffle(jobs)

    responses = await asyncio.gather(
        *(post_bid(client, auth(settings, u), a.id, amount) for a, u, amount in jobs)
    )

    accepted: dict[uuid.UUID, list[int]] = {a.id: [] for a in auctions}
    for (a, _, _), r in zip(jobs, responses, strict=True):
        if r.status_code == 201:
            accepted[a.id].append(r.json()["bid"]["amount"])
    for a in auctions:
        assert accepted[a.id], "every auction should have received at least one bid"
        await assert_consistent(sessionmaker, a.id, accepted[a.id])


async def test_concurrent_retries_with_one_idempotency_key_create_one_bid(
    client, clock, sessionmaker, settings, publisher
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    h = auth(settings, user)

    responses = await asyncio.gather(
        *(
            post_bid(client, h, auction.id, auction.starting_price, key="double-click-key-1")
            for _ in range(12)
        )
    )

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["bid"]["id"] for r in responses}) == 1
    assert len(publisher.of_type("bid_placed")) == 2  # one bid -> auction room + lobby, once
    await assert_consistent(sessionmaker, auction.id, [auction.starting_price])


async def test_unique_constraint_is_the_last_line_of_defence(sessionmaker, clock) -> None:
    """Even if the row lock were bypassed, the schema refuses a duplicate amount."""
    from sqlalchemy.exc import IntegrityError

    auction = await make_auction(sessionmaker, now=clock.current)
    a, b = await make_user(sessionmaker), await make_user(sessionmaker)
    async with sessionmaker() as s, s.begin():
        s.add(Bid(auction_id=auction.id, bidder_id=a.id, amount=auction.starting_price))
    with pytest.raises(IntegrityError):
        async with sessionmaker() as s, s.begin():
            s.add(Bid(auction_id=auction.id, bidder_id=b.id, amount=auction.starting_price))


# --------------------------------------------------------------------------- close vs bid
async def _db_now(sessionmaker):  # type: ignore[no-untyped-def]
    async with sessionmaker() as s:
        return (await s.execute(text("SELECT clock_timestamp()"))).scalar_one()


async def _bidder(client, headers, auction_id, start_amount: int, inc: int) -> list[int]:  # type: ignore[no-untyped-def]
    """Bid relentlessly until the auction reports it has ended; return accepted amounts."""
    accepted: list[int] = []
    guess = start_amount
    while True:
        r = await post_bid(client, headers, auction_id, guess)
        if r.status_code == 201:
            accepted.append(guess)
            guess += inc * random.randint(1, 3)
            await asyncio.sleep(0.01)
            continue
        code = r.json()["code"]
        if code == "AUCTION_ENDED":
            return accepted
        if code == "BID_TOO_LOW":
            guess = r.json()["min_next_bid"]
        else:  # ALREADY_HIGHEST: let someone else lead for a moment
            guess += inc
            await asyncio.sleep(0.02)


async def _run_race(real_clock_app, sessionmaker, settings, *, snipe: bool):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    # Do ALL slow setup first and create the auction last: the auction's clock starts when its row
    # is written, so setup time must never eat into the window the bidders are racing in (a loaded
    # machine once made the auction end before the first bid landed).
    users = [await make_user(sessionmaker) for _ in range(8)]
    scheduler = real_clock_app.state.scheduler
    now = await _db_now(sessionmaker)
    auction = await make_auction(
        sessionmaker,
        now=now,
        start_in=timedelta(seconds=-1),
        duration=timedelta(seconds=5 if snipe else 4),  # end_at = now + 3 s (+1 s when sniping)
        snipe_window_s=2 if snipe else 0,
        snipe_extension_s=1,
        max_extension_s=3,
    )
    stop = asyncio.Event()

    async def ticker() -> None:
        while not stop.is_set():
            await scheduler.tick()
            await asyncio.sleep(0.02)

    transport = httpx.ASGITransport(app=real_clock_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        tick_task = asyncio.create_task(ticker())
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        _bidder(
                            client,
                            auth(settings, u),
                            auction.id,
                            auction.starting_price,
                            auction.min_increment,
                        )
                        for u in users
                    )
                ),
                timeout=40,
            )
        finally:
            await asyncio.sleep(0.1)
            stop.set()
            await tick_task
    return auction, [amount for r in results for amount in r]


@pytest.mark.parametrize("snipe", [False, True], ids=["fixed-end", "sniping-war"])
async def test_nothing_commits_after_close_and_winner_is_final_leader(
    real_clock_app, sessionmaker, settings, snipe
) -> None:
    auction, accepted = await _run_race(real_clock_app, sessionmaker, settings, snipe=snipe)

    async with sessionmaker() as s:
        row = await s.get(Auction, auction.id)
        kinds = list(
            (
                await s.execute(
                    select(AuctionEvent.type)
                    .where(AuctionEvent.auction_id == auction.id)
                    .order_by(AuctionEvent.id)
                )
            ).scalars()
        )
        bids = (await s.execute(select(Bid).where(Bid.auction_id == auction.id))).scalars().all()

    assert row.status == "ENDED"  # type: ignore[union-attr]
    assert kinds.count("ended") == 1, "closed exactly once"
    assert "bid_placed" not in kinds[kinds.index("ended") :], "I6: a bid committed after the close"
    assert all(b.created_at < row.ended_at for b in bids), "I6: bid timestamped after close"  # type: ignore[union-attr,operator]
    assert row.ended_at >= row.end_at, "closed before its (extended) end time"  # type: ignore[union-attr,operator]
    cap = row.original_end_at.timestamp() + auction.max_extension_s  # type: ignore[union-attr]
    assert row.end_at.timestamp() <= cap + 0.001, "extension cap exceeded"  # type: ignore[union-attr]
    if not snipe:
        assert row.end_at == auction.end_at, "no extensions when the window is disabled"  # type: ignore[union-attr]
    assert accepted, "bidders should have landed bids before the end"
    top = max(bids, key=lambda b: b.amount)
    assert (row.outcome, row.winner_id, row.current_price) == ("SOLD", top.bidder_id, top.amount)  # type: ignore[union-attr]
    await assert_consistent(sessionmaker, auction.id, accepted, start_version=1)  # +1: LIVE->ENDED
