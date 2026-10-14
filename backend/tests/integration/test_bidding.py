from datetime import timedelta

import httpx
from sqlalchemy import func, select

from app.core.clock import FakeClock
from app.models.auction import Auction, AuctionEvent, Bid
from app.realtime.publisher import RecordingPublisher
from tests.factories import RUPEE, auth, make_auction, make_user


async def bid(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    auction_id: object,
    amount: int,
    key: str | None = None,
) -> httpx.Response:
    h = {**headers, **({"Idempotency-Key": key} if key else {})}
    return await client.post(
        f"/api/v1/auctions/{auction_id}/bids", json={"amount": amount}, headers=h
    )


async def test_first_bid_at_starting_price_is_accepted(
    client, clock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)

    r = await bid(client, auth(settings, user), auction.id, auction.starting_price)

    assert r.status_code == 201
    body = r.json()
    assert body["bid"]["amount"] == auction.starting_price
    assert body["auction"]["current_price"] == auction.starting_price
    assert body["auction"]["bid_count"] == 1
    assert body["auction"]["viewer"]["is_leading"] is True
    assert body["auction"]["min_next_bid"] == auction.starting_price + auction.min_increment


async def test_bid_below_minimum_is_rejected_with_actionable_error(
    client, clock, sessionmaker, settings
) -> None:
    a, b = await make_user(sessionmaker), await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    await bid(client, auth(settings, a), auction.id, auction.starting_price)

    r = await bid(client, auth(settings, b), auction.id, auction.starting_price + 1)

    assert r.status_code == 409
    assert r.headers["content-type"].startswith("application/problem+json")
    problem = r.json()
    assert problem["code"] == "BID_TOO_LOW"
    assert problem["min_next_bid"] == auction.starting_price + auction.min_increment
    assert problem["request_id"] == r.headers["x-request-id"]


async def test_leader_cannot_outbid_themselves(client, clock, sessionmaker, settings) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    h = auth(settings, user)
    await bid(client, h, auction.id, auction.starting_price)

    r = await bid(client, h, auction.id, auction.starting_price * 3)

    assert r.status_code == 409 and r.json()["code"] == "ALREADY_HIGHEST"


async def test_outbid_user_is_notified_privately_and_everyone_gets_the_public_event(
    client, clock, sessionmaker, settings, publisher: RecordingPublisher
) -> None:
    a, b = await make_user(sessionmaker), await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    await bid(client, auth(settings, a), auction.id, auction.starting_price)
    publisher.events.clear()

    r = await bid(
        client, auth(settings, b), auction.id, auction.starting_price + auction.min_increment
    )

    assert r.status_code == 201
    rooms = {room for room, _ in publisher.events}
    assert rooms == {f"auction:{auction.id}", "lobby", f"user:{a.id}"}
    outbid = publisher.of_type("outbid")
    assert len(outbid) == 1 and outbid[0][0] == f"user:{a.id}"
    assert outbid[0][1]["data"]["your_amount"] == auction.starting_price
    public = publisher.of_type("bid_placed")[0][1]
    assert public["version"] == r.json()["auction"]["version"]
    assert "bidder_id" not in str(public) and str(b.id) not in str(public)  # identity stays masked


async def test_rejected_bids_publish_nothing(
    client, clock, sessionmaker, settings, publisher
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    r = await bid(client, auth(settings, user), auction.id, 1)
    assert r.status_code == 409
    assert publisher.events == []


async def test_bid_not_allowed_before_start(client, clock, sessionmaker, settings) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(
        sessionmaker, now=clock.current, status="SCHEDULED", start_in=timedelta(hours=1)
    )
    r = await bid(client, auth(settings, user), auction.id, auction.starting_price)
    assert r.status_code == 409 and r.json()["code"] == "AUCTION_NOT_LIVE"


async def test_bid_after_end_is_rejected_even_if_scheduler_has_not_closed_it(
    client, clock: FakeClock, sessionmaker, settings
) -> None:
    """The clock decides, not the worker: status is still LIVE here."""
    user = await make_user(sessionmaker)
    auction = await make_auction(
        sessionmaker,
        now=clock.current,
        duration=timedelta(minutes=5),
        start_in=timedelta(minutes=-1),
    )
    clock.current = auction.end_at  # exactly at the end

    r = await bid(client, auth(settings, user), auction.id, auction.starting_price)

    assert r.status_code == 409 and r.json()["code"] == "AUCTION_ENDED"
    async with sessionmaker() as s:
        assert (await s.get(Auction, auction.id)).status == "LIVE"  # type: ignore[union-attr]


async def test_draft_auctions_are_invisible_to_bidders(
    client, clock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current, status="DRAFT")
    r = await bid(client, auth(settings, user), auction.id, auction.starting_price)
    assert r.status_code == 404
    assert (await client.get(f"/api/v1/auctions/{auction.id}")).status_code == 404


async def test_admins_cannot_bid(client, clock, sessionmaker, settings) -> None:
    admin = await make_user(sessionmaker, role="ADMIN")
    auction = await make_auction(sessionmaker, now=clock.current)
    r = await bid(client, auth(settings, admin), auction.id, auction.starting_price)
    assert r.status_code == 403 and r.json()["code"] == "ADMIN_CANNOT_BID"


async def test_bidding_requires_authentication(client, clock, sessionmaker) -> None:
    auction = await make_auction(sessionmaker, now=clock.current)
    r = await client.post(
        f"/api/v1/auctions/{auction.id}/bids", json={"amount": auction.starting_price}
    )
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"


async def test_amount_validation(client, clock, sessionmaker, settings) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    h = auth(settings, user)
    for payload in ({"amount": 0}, {"amount": -5}, {"amount": "lots"}, {"amount": 1.5}, {}):
        r = await client.post(f"/api/v1/auctions/{auction.id}/bids", json=payload, headers=h)
        assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR", payload


async def test_fat_finger_cap(client, clock, sessionmaker, settings) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    r = await bid(client, auth(settings, user), auction.id, settings.max_bid_amount + 1)
    assert r.status_code == 422 and r.json()["code"] == "BID_TOO_HIGH"


# --------------------------------------------------------------------------- idempotency
async def test_retry_with_same_idempotency_key_creates_one_bid(
    client, clock, sessionmaker, settings, publisher
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    h = auth(settings, user)

    first = await bid(client, h, auction.id, auction.starting_price, key="retry-key-0001")
    publisher.events.clear()
    second = await bid(client, h, auction.id, auction.starting_price, key="retry-key-0001")

    assert first.status_code == second.status_code == 201
    assert first.json()["bid"]["id"] == second.json()["bid"]["id"]
    assert publisher.events == []  # the replay must not re-announce the bid
    async with sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(Bid))).scalar_one() == 1


async def test_reusing_a_key_for_a_different_bid_is_a_conflict(
    client, clock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    h = auth(settings, user)
    await bid(client, h, auction.id, auction.starting_price, key="same-key-0001")

    r = await bid(client, h, auction.id, auction.starting_price + 10 * RUPEE, key="same-key-0001")

    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_CONFLICT"


async def test_malformed_idempotency_key_is_rejected(client, clock, sessionmaker, settings) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    r = await bid(client, auth(settings, user), auction.id, auction.starting_price, key="short")
    assert r.status_code == 422


# --------------------------------------------------------------------------- anti-sniping
async def test_bid_inside_final_window_extends_the_auction(
    client, clock: FakeClock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(
        sessionmaker, now=clock.current, snipe_window_s=120, snipe_extension_s=120
    )
    clock.current = auction.end_at - timedelta(seconds=30)

    r = await bid(client, auth(settings, user), auction.id, auction.starting_price)

    assert r.status_code == 201 and r.json()["extended"] is True
    new_end = clock.current + timedelta(seconds=120)
    async with sessionmaker() as s:
        row = await s.get(Auction, auction.id)
        assert row.end_at == new_end  # type: ignore[union-attr]
        assert row.original_end_at == auction.original_end_at  # type: ignore[union-attr]
        kinds = list(
            (await s.execute(select(AuctionEvent.type).order_by(AuctionEvent.id))).scalars()
        )
        assert kinds == ["bid_placed", "extended"]


async def test_bid_outside_window_does_not_extend(client, clock, sessionmaker, settings) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    r = await bid(client, auth(settings, user), auction.id, auction.starting_price)
    assert r.json()["extended"] is False
    async with sessionmaker() as s:
        assert (await s.get(Auction, auction.id)).end_at == auction.end_at  # type: ignore[union-attr]


async def test_extensions_stop_at_the_cap(client, clock: FakeClock, sessionmaker, settings) -> None:
    a, b = await make_user(sessionmaker), await make_user(sessionmaker)
    auction = await make_auction(
        sessionmaker,
        now=clock.current,
        snipe_window_s=120,
        snipe_extension_s=120,
        max_extension_s=200,
    )
    cap = auction.original_end_at + timedelta(seconds=200)
    users, price = [a, b], auction.starting_price
    # Keep sniping in the last 10 seconds until the cap is hit.
    for i in range(10):
        async with sessionmaker() as s:
            current_end = (await s.get(Auction, auction.id)).end_at  # type: ignore[union-attr]
        clock.current = current_end - timedelta(seconds=10)
        r = await bid(client, auth(settings, users[i % 2]), auction.id, price)
        assert r.status_code == 201
        price += auction.min_increment
    async with sessionmaker() as s:
        assert (await s.get(Auction, auction.id)).end_at == cap  # type: ignore[union-attr]
