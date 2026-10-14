from datetime import timedelta

import pytest
from sqlalchemy import update

from app.models.auction import Auction
from tests.factories import RUPEE, auth, make_auction, make_bike, make_user
from tests.integration.test_bidding import bid


async def ids(client, **params):  # type: ignore[no-untyped-def]
    r = await client.get("/api/v1/auctions", params=params)
    assert r.status_code == 200, r.text
    return [a["id"] for a in r.json()["items"]]


async def test_default_listing_shows_only_live_auctions(client, clock, sessionmaker) -> None:
    live = await make_auction(sessionmaker, now=clock.current)
    upcoming = await make_auction(
        sessionmaker, now=clock.current, status="SCHEDULED", start_in=timedelta(hours=1)
    )
    await make_auction(sessionmaker, now=clock.current, status="DRAFT")
    await make_auction(sessionmaker, now=clock.current, status="CANCELLED")
    ended = await make_auction(sessionmaker, now=clock.current, status="ENDED")

    assert await ids(client) == [str(live.id)]
    assert await ids(client, status="SCHEDULED") == [str(upcoming.id)]
    assert await ids(client, status="ENDED") == [str(ended.id)]
    assert set(await ids(client, status=["LIVE", "SCHEDULED", "ENDED"])) == {
        str(live.id),
        str(upcoming.id),
        str(ended.id),
    }


@pytest.mark.parametrize("status", ["DRAFT", "CANCELLED", "bogus"])
async def test_non_public_statuses_cannot_be_requested(client, status) -> None:
    assert (await client.get("/api/v1/auctions", params={"status": status})).status_code == 422


async def test_filters(client, clock, sessionmaker) -> None:
    now = clock.current
    classic = await make_auction(
        sessionmaker,
        now=now,
        bike=await make_bike(sessionmaker, make="Royal Enfield", model="Classic 350", year=2021),
        starting_price=90_000 * RUPEE,
    )
    duke = await make_auction(
        sessionmaker,
        now=now,
        bike=await make_bike(sessionmaker, make="KTM", model="Duke 390", year=2018),
        starting_price=150_000 * RUPEE,
    )
    pulsar = await make_auction(
        sessionmaker,
        now=now,
        bike=await make_bike(sessionmaker, make="Bajaj", model="Pulsar NS200", year=2020),
        starting_price=60_000 * RUPEE,
    )

    assert await ids(client, make="ktm") == [str(duke.id)]
    assert await ids(client, q="classic") == [str(classic.id)]
    assert await ids(client, q="PULS") == [str(pulsar.id)]
    assert set(await ids(client, year_min=2020)) == {str(classic.id), str(pulsar.id)}
    assert await ids(client, year_max=2018) == [str(duke.id)]
    assert set(await ids(client, price_min=80_000 * RUPEE, price_max=150_000 * RUPEE)) == {
        str(classic.id),
        str(duke.id),
    }
    assert await ids(client, q="nothing-matches") == []


async def test_search_treats_wildcards_literally(client, clock, sessionmaker) -> None:
    await make_auction(sessionmaker, now=clock.current)
    assert await ids(client, q="%") == []
    assert await ids(client, q="_") == []


@pytest.mark.parametrize("sort", ["ending_soon", "newest", "price_low", "price_high"])
async def test_keyset_pagination_visits_every_row_exactly_once(
    client, clock, sessionmaker, sort
) -> None:
    created = []
    for i in range(11):
        created.append(
            await make_auction(
                sessionmaker,
                now=clock.current,
                start_in=timedelta(minutes=-i),
                duration=timedelta(minutes=30 + (i % 4) * 5),
                starting_price=(50 + i % 3) * 1000 * RUPEE,
            )
        )
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"sort": sort, "limit": 4, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/api/v1/auctions", params=params)).json()
        seen += [a["id"] for a in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 3
    assert sorted(seen) == sorted(str(a.id) for a in created) and len(set(seen)) == 11


async def test_sort_orders(client, clock, sessionmaker) -> None:
    soon = await make_auction(
        sessionmaker,
        now=clock.current,
        duration=timedelta(minutes=20),
        starting_price=70_000 * RUPEE,
    )
    later = await make_auction(
        sessionmaker,
        now=clock.current,
        duration=timedelta(minutes=50),
        starting_price=40_000 * RUPEE,
    )
    assert await ids(client, sort="ending_soon") == [str(soon.id), str(later.id)]
    assert await ids(client, sort="price_low") == [str(later.id), str(soon.id)]
    assert await ids(client, sort="price_high") == [str(soon.id), str(later.id)]


async def test_bad_cursor_and_limit(client) -> None:
    assert (await client.get("/api/v1/auctions", params={"cursor": "garbage"})).status_code == 422
    assert (await client.get("/api/v1/auctions", params={"limit": 0})).status_code == 422
    assert (await client.get("/api/v1/auctions", params={"limit": 500})).status_code == 422


async def test_listing_marks_the_viewers_own_standing(
    client, clock, sessionmaker, settings
) -> None:
    me, rival = await make_user(sessionmaker), await make_user(sessionmaker)
    mine = await make_auction(sessionmaker, now=clock.current)
    lost = await make_auction(sessionmaker, now=clock.current)
    untouched = await make_auction(sessionmaker, now=clock.current)
    await bid(client, auth(settings, me), mine.id, mine.starting_price)
    await bid(client, auth(settings, me), lost.id, lost.starting_price)
    await bid(client, auth(settings, rival), lost.id, lost.starting_price + lost.min_increment)

    r = await client.get("/api/v1/auctions", headers=auth(settings, me))
    by_id = {a["id"]: a["viewer"] for a in r.json()["items"]}
    assert (
        by_id[str(mine.id)]["is_leading"] is True
        and by_id[str(mine.id)]["my_highest_bid"] == mine.starting_price
    )
    assert (
        by_id[str(lost.id)]["is_leading"] is False
        and by_id[str(lost.id)]["my_highest_bid"] == lost.starting_price
    )
    assert by_id[str(untouched.id)]["my_highest_bid"] is None
    anonymous = (await client.get("/api/v1/auctions")).json()["items"]
    assert all(a["viewer"] is None for a in anonymous)


async def test_makes_lists_only_publicly_visible_brands(client, clock, sessionmaker) -> None:
    await make_auction(
        sessionmaker, now=clock.current, bike=await make_bike(sessionmaker, make="KTM")
    )
    await make_auction(
        sessionmaker, now=clock.current, bike=await make_bike(sessionmaker, make="Honda")
    )
    await make_auction(
        sessionmaker,
        now=clock.current,
        status="DRAFT",
        bike=await make_bike(sessionmaker, make="Secret"),
    )
    assert (await client.get("/api/v1/auctions/makes")).json() == ["Honda", "KTM"]


async def test_bid_history_is_newest_first_masked_and_paginated(
    client, clock, sessionmaker, settings
) -> None:
    a, b = await make_user(sessionmaker), await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    amounts = []
    for i in range(6):
        user = (a, b)[i % 2]
        amount = auction.starting_price + i * auction.min_increment
        await bid(client, auth(settings, user), auction.id, amount)
        amounts.append(amount)

    first = (await client.get(f"/api/v1/auctions/{auction.id}/bids", params={"limit": 4})).json()
    assert [x["amount"] for x in first["items"]] == amounts[::-1][:4]
    second = (
        await client.get(
            f"/api/v1/auctions/{auction.id}/bids",
            params={"limit": 4, "cursor": first["next_cursor"]},
        )
    ).json()
    assert [x["amount"] for x in second["items"]] == amounts[::-1][4:] and second[
        "next_cursor"
    ] is None
    assert all(set(x) == {"id", "amount", "created_at", "bidder_alias"} for x in first["items"])
    assert len({x["bidder_alias"] for x in first["items"]}) == 2  # stable alias per bidder


async def test_unknown_or_draft_auction_history_is_404(client, clock, sessionmaker) -> None:
    draft = await make_auction(sessionmaker, now=clock.current, status="DRAFT")
    assert (await client.get(f"/api/v1/auctions/{draft.id}/bids")).status_code == 404
    assert (
        await client.get("/api/v1/auctions/00000000-0000-0000-0000-000000000000")
    ).status_code == 404
    assert (await client.get("/api/v1/auctions/not-a-uuid")).status_code == 422


async def test_my_bids_classifies_every_outcome(client, app, clock, sessionmaker, settings) -> None:
    me, rival = await make_user(sessionmaker), await make_user(sessionmaker)
    h_me, h_rival = auth(settings, me), auth(settings, rival)
    leading = await make_auction(sessionmaker, now=clock.current)
    outbid = await make_auction(sessionmaker, now=clock.current)
    won = await make_auction(
        sessionmaker,
        now=clock.current,
        start_in=timedelta(minutes=-1),
        duration=timedelta(minutes=5),
    )
    lost = await make_auction(
        sessionmaker,
        now=clock.current,
        start_in=timedelta(minutes=-1),
        duration=timedelta(minutes=5),
    )
    cancelled = await make_auction(sessionmaker, now=clock.current)

    await bid(client, h_me, leading.id, leading.starting_price)
    await bid(client, h_me, outbid.id, outbid.starting_price)
    await bid(client, h_rival, outbid.id, outbid.starting_price + outbid.min_increment)
    await bid(client, h_me, won.id, won.starting_price)
    await bid(client, h_me, lost.id, lost.starting_price)
    await bid(client, h_rival, lost.id, lost.starting_price + lost.min_increment)
    await bid(client, h_me, cancelled.id, cancelled.starting_price)
    async with sessionmaker() as s, s.begin():
        await s.execute(
            update(Auction).where(Auction.id == cancelled.id).values(status="CANCELLED")
        )

    clock.current = won.end_at + timedelta(seconds=1)
    await app.state.scheduler.tick()

    rows = (await client.get("/api/v1/me/bids", headers=h_me)).json()
    results = {r["auction"]["id"]: r["result"] for r in rows}
    assert results == {
        str(leading.id): "LEADING",
        str(outbid.id): "OUTBID",
        str(won.id): "WON",
        str(lost.id): "LOST",
        str(cancelled.id): "CANCELLED",
    }
    assert all(r["my_highest_bid"] >= r["auction"]["starting_price"] for r in rows)
    assert (await client.get("/api/v1/me/bids")).status_code == 401
    assert (
        await client.get("/api/v1/me/bids", headers=h_rival)
    ).json() != rows  # strictly per-user
