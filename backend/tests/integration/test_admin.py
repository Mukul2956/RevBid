from datetime import timedelta

import pytest

from tests.factories import RUPEE, auth, make_auction, make_bike, make_user

BIKE = {
    "make": "KTM",
    "model": "Duke 390",
    "year": 2019,
    "km_driven": 21_000,
    "owner_number": 1,
    "condition": "GOOD",
    "inspection_score": 82,
    "description": "Clean",
    "images": ["https://img.test/a.jpg"],
}


@pytest.fixture
async def admin_headers(sessionmaker, settings):  # type: ignore[no-untyped-def]
    return auth(settings, await make_user(sessionmaker, role="ADMIN"))


def auction_payload(bike_id, clock, **overrides):  # type: ignore[no-untyped-def]
    start = clock.current + timedelta(hours=1)
    base = {
        "bike_id": str(bike_id),
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=2)).isoformat(),
        "starting_price": 50_000 * RUPEE,
        "min_increment": 500 * RUPEE,
    }
    return {**base, **overrides}


# --------------------------------------------------------------------------- RBAC
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/admin/stats"),
        ("get", "/api/v1/admin/system"),
        ("get", "/api/v1/admin/bikes"),
        ("post", "/api/v1/admin/bikes"),
        ("get", "/api/v1/admin/auctions"),
        ("post", "/api/v1/admin/auctions"),
    ],
)
async def test_admin_routes_require_admin(client, sessionmaker, settings, method, path) -> None:
    anonymous = await getattr(client, method)(path)
    assert anonymous.status_code == 401
    bidder = auth(settings, await make_user(sessionmaker))
    forbidden = await getattr(client, method)(path, headers=bidder)
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN"


# --------------------------------------------------------------------------- bikes
async def test_bike_crud(client, admin_headers) -> None:
    created = await client.post("/api/v1/admin/bikes", json=BIKE, headers=admin_headers)
    assert created.status_code == 201
    bike_id = created.json()["id"]

    patched = await client.patch(
        f"/api/v1/admin/bikes/{bike_id}",
        json={"km_driven": 22_000, "color": None},
        headers=admin_headers,
    )
    assert patched.status_code == 200 and patched.json()["km_driven"] == 22_000

    listed = await client.get("/api/v1/admin/bikes", params={"q": "duke"}, headers=admin_headers)
    assert [b["id"] for b in listed.json()["items"]] == [bike_id]

    assert (
        await client.delete(f"/api/v1/admin/bikes/{bike_id}", headers=admin_headers)
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/admin/bikes/{bike_id}", headers=admin_headers)
    ).status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        {"year": 1900},
        {"km_driven": -1},
        {"inspection_score": 101},
        {"images": ["javascript:alert(1)"]},
        {"images": ["data:text/html;base64,AAAA"]},
        {"images": ["https://x.test/a.jpg"] * 9},
        {"make": ""},
    ],
)
async def test_bike_validation(client, admin_headers, bad) -> None:
    r = await client.post("/api/v1/admin/bikes", json={**BIKE, **bad}, headers=admin_headers)
    assert r.status_code == 422


async def test_bike_with_an_auction_cannot_be_deleted(
    client, admin_headers, sessionmaker, clock
) -> None:
    auction = await make_auction(sessionmaker, now=clock.current)
    r = await client.delete(f"/api/v1/admin/bikes/{auction.bike_id}", headers=admin_headers)
    assert r.status_code == 409


async def test_bike_pagination_is_stable(client, admin_headers, sessionmaker) -> None:
    ids = [str((await make_bike(sessionmaker, model=f"M{i}")).id) for i in range(7)]
    seen: list[str] = []
    cursor = None
    while True:
        r = await client.get(
            "/api/v1/admin/bikes",
            params={"limit": 3, **({"cursor": cursor} if cursor else {})},
            headers=admin_headers,
        )
        page = r.json()
        seen += [b["id"] for b in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == sorted(ids) and len(set(seen)) == 7


# --------------------------------------------------------------------------- auction commands
async def test_create_schedule_and_audit_trail(
    client, admin_headers, sessionmaker, clock, publisher
) -> None:
    bike = await make_bike(sessionmaker)
    created = await client.post(
        "/api/v1/admin/auctions", json=auction_payload(bike.id, clock), headers=admin_headers
    )
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "DRAFT" and body["reserve_price"] is None
    assert body["current_price"] == body["starting_price"] and body["snipe_window_s"] == 120
    assert publisher.events == []  # drafts are invisible: nothing is broadcast

    scheduled = await client.post(
        f"/api/v1/admin/auctions/{body['id']}/schedule", headers=admin_headers
    )
    assert scheduled.status_code == 200 and scheduled.json()["status"] == "SCHEDULED"
    assert {r for r, _ in publisher.events} == {f"auction:{body['id']}", "lobby"}

    events = await client.get(f"/api/v1/admin/auctions/{body['id']}/events", headers=admin_headers)
    assert [e["type"] for e in events.json()] == ["scheduled", "created"]  # newest first


async def test_create_and_schedule_in_one_step(client, admin_headers, sessionmaker, clock) -> None:
    bike = await make_bike(sessionmaker)
    r = await client.post(
        "/api/v1/admin/auctions",
        json=auction_payload(bike.id, clock, schedule=True),
        headers=admin_headers,
    )
    assert r.status_code == 201 and r.json()["status"] == "SCHEDULED"


@pytest.mark.parametrize(
    ("override", "status"),
    [
        ({"starting_price": 0}, 422),
        ({"min_increment": -1}, 422),
        ({"reserve_price": 1}, 422),  # below starting price
        ({"start_at": "2030-01-01T12:00:00"}, 422),  # naive timestamp
    ],
)
async def test_auction_input_validation(
    client, admin_headers, sessionmaker, clock, override, status
) -> None:
    bike = await make_bike(sessionmaker)
    r = await client.post(
        "/api/v1/admin/auctions",
        json=auction_payload(bike.id, clock, **override),
        headers=admin_headers,
    )
    assert r.status_code == status


async def test_schedule_window_rules(client, admin_headers, sessionmaker, clock) -> None:
    bike = await make_bike(sessionmaker)
    start = clock.current + timedelta(hours=1)
    too_short = auction_payload(bike.id, clock, end_at=(start + timedelta(seconds=30)).isoformat())
    assert (
        await client.post("/api/v1/admin/auctions", json=too_short, headers=admin_headers)
    ).status_code == 422
    in_past = auction_payload(
        bike.id,
        clock,
        start_at=(clock.current - timedelta(hours=2)).isoformat(),
        end_at=(clock.current - timedelta(hours=1)).isoformat(),
    )
    r = await client.post("/api/v1/admin/auctions", json=in_past, headers=admin_headers)
    assert r.status_code == 422 and r.json()["code"] == "INVALID_SCHEDULE"


async def test_a_bike_cannot_have_two_open_auctions(
    client, admin_headers, sessionmaker, clock
) -> None:
    bike = await make_bike(sessionmaker)
    payload = auction_payload(bike.id, clock)
    assert (
        await client.post("/api/v1/admin/auctions", json=payload, headers=admin_headers)
    ).status_code == 201
    dup = await client.post("/api/v1/admin/auctions", json=payload, headers=admin_headers)
    assert dup.status_code == 409 and dup.json()["code"] == "BIKE_ALREADY_LISTED"


async def test_bike_can_be_relisted_after_its_auction_ended(
    client, admin_headers, sessionmaker, clock, app
) -> None:
    auction = await make_auction(sessionmaker, now=clock.current)
    clock.advance(hours=2)
    await app.state.scheduler.tick()
    r = await client.post(
        "/api/v1/admin/auctions",
        json=auction_payload(auction.bike_id, clock),
        headers=admin_headers,
    )
    assert r.status_code == 201


async def test_edit_only_before_going_live(client, admin_headers, sessionmaker, clock) -> None:
    draft = await make_auction(
        sessionmaker, now=clock.current, status="DRAFT", start_in=timedelta(hours=1)
    )
    ok = await client.patch(
        f"/api/v1/admin/auctions/{draft.id}",
        json={"starting_price": 70_000 * RUPEE, "reserve_price": 90_000 * RUPEE},
        headers=admin_headers,
    )
    assert ok.status_code == 200
    assert ok.json()["starting_price"] == ok.json()["current_price"] == 70_000 * RUPEE

    cleared = await client.patch(
        f"/api/v1/admin/auctions/{draft.id}", json={"clear_reserve": True}, headers=admin_headers
    )
    assert cleared.json()["reserve_price"] is None

    bad = await client.patch(
        f"/api/v1/admin/auctions/{draft.id}", json={"reserve_price": 1}, headers=admin_headers
    )
    assert bad.status_code == 422

    live = await make_auction(sessionmaker, now=clock.current)
    locked = await client.patch(
        f"/api/v1/admin/auctions/{live.id}", json={"starting_price": 1}, headers=admin_headers
    )
    assert locked.status_code == 409 and locked.json()["code"] == "AUCTION_NOT_EDITABLE"


async def test_cancel_live_auction_blocks_bids_and_notifies(
    client, admin_headers, sessionmaker, clock, settings, publisher
) -> None:
    bidder = await make_user(sessionmaker)
    live = await make_auction(sessionmaker, now=clock.current)
    publisher.events.clear()

    r = await client.post(
        f"/api/v1/admin/auctions/{live.id}/cancel",
        json={"reason": "Seller withdrew"},
        headers=admin_headers,
    )

    assert r.status_code == 200 and r.json()["status"] == "CANCELLED"
    assert {room for room, _ in publisher.of_type("auction_cancelled")} == {
        f"auction:{live.id}",
        "lobby",
    }
    bid = await client.post(
        f"/api/v1/auctions/{live.id}/bids",
        json={"amount": live.starting_price},
        headers=auth(settings, bidder),
    )
    assert bid.status_code == 409 and bid.json()["code"] == "AUCTION_ENDED"


async def test_invalid_transitions_are_refused(client, admin_headers, sessionmaker, clock) -> None:
    live = await make_auction(sessionmaker, now=clock.current)
    r = await client.post(f"/api/v1/admin/auctions/{live.id}/schedule", headers=admin_headers)
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"

    await client.post(
        f"/api/v1/admin/auctions/{live.id}/cancel",
        json={"reason": "no longer available"},
        headers=admin_headers,
    )
    again = await client.post(
        f"/api/v1/admin/auctions/{live.id}/cancel",
        json={"reason": "second time"},
        headers=admin_headers,
    )
    assert again.status_code == 409 and again.json()["code"] == "INVALID_TRANSITION"


async def test_cancel_requires_a_reason(client, admin_headers, sessionmaker, clock) -> None:
    live = await make_auction(sessionmaker, now=clock.current)
    assert (
        await client.post(
            f"/api/v1/admin/auctions/{live.id}/cancel", json={"reason": ""}, headers=admin_headers
        )
    ).status_code == 422


async def test_unknown_auction_is_404(client, admin_headers) -> None:
    r = await client.post(
        "/api/v1/admin/auctions/00000000-0000-0000-0000-000000000000/schedule",
        headers=admin_headers,
    )
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


async def test_admin_view_exposes_reserve_but_public_view_never_does(
    client, admin_headers, sessionmaker, clock
) -> None:
    auction = await make_auction(sessionmaker, now=clock.current, reserve_price=80_000 * RUPEE)
    admin_view = (
        await client.get(f"/api/v1/admin/auctions/{auction.id}", headers=admin_headers)
    ).json()
    public_view = (await client.get(f"/api/v1/auctions/{auction.id}")).json()
    listing = (await client.get("/api/v1/auctions")).text

    assert admin_view["reserve_price"] == 80_000 * RUPEE
    assert "reserve_price" not in public_view and public_view["has_reserve"] is True
    assert str(80_000 * RUPEE) not in str(public_view) and str(80_000 * RUPEE) not in listing
    assert public_view["reserve_met"] is False


async def test_admin_stats_and_system(client, admin_headers, sessionmaker, clock) -> None:
    await make_auction(sessionmaker, now=clock.current)
    stats = (await client.get("/api/v1/admin/stats", headers=admin_headers)).json()
    assert stats["auctions_by_status"]["LIVE"] == 1 and stats["users"] == 1
    system = (await client.get("/api/v1/admin/system", headers=admin_headers)).json()
    assert (
        system["database"]["ok"] and system["redis"]["ok"] and system["websocket_connections"] == 0
    )
    assert system["version"] and system["uptime_s"] >= 0
