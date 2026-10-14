"""WebSocket protocol tests: real Redis pub/sub -> hub -> socket, over an in-process ASGI transport.

The HTTP client is created inside each test (``ws_client``) rather than in a fixture: anyio-based
context managers must be entered and exited in the same task, which fixture teardown is not.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from httpx_ws import WebSocketDisconnect, WebSocketUpgradeError, aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport

from app.core.clock import FakeClock
from tests.factories import auth, make_auction, make_user

WS = "http://test/ws"
ORIGIN = {"Origin": "http://localhost:5173"}


@asynccontextmanager
async def ws_client(app):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://test"
    ) as c:
        yield c


def token(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


async def recv(ws):  # type: ignore[no-untyped-def]
    return await ws.receive_json(timeout=3)


async def subscribe(ws, room: str) -> None:  # type: ignore[no-untyped-def]
    await ws.send_json({"type": "subscribe", "room": room})
    assert await recv(ws) == {"type": "subscribed", "room": room}


async def authenticate(ws, headers) -> None:  # type: ignore[no-untyped-def]
    await ws.send_json({"type": "auth", "token": token(headers)})
    assert (await recv(ws))["type"] == "authed"


async def place(http, headers, auction_id, amount):  # type: ignore[no-untyped-def]
    r = await http.post(
        f"/api/v1/auctions/{auction_id}/bids", json={"amount": amount}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def test_subscriber_receives_bid_events_in_real_time(
    live_app, clock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    async with ws_client(live_app) as http, aconnect_ws(WS, http, headers=ORIGIN) as ws:
        await subscribe(ws, f"auction:{auction.id}")

        body = await place(http, auth(settings, user), auction.id, auction.starting_price)

        event = await recv(ws)
        assert event["type"] == "bid_placed" and event["auction_id"] == str(auction.id)
        assert event["version"] == body["auction"]["version"]
        assert event["data"]["current_price"] == auction.starting_price
        assert event["data"]["leader_alias"] == body["bid"]["bidder_alias"]
        assert event["data"]["min_next_bid"] == auction.starting_price + auction.min_increment
        assert "server_time" in event
        assert str(user.id) not in json.dumps(event), "bidder identity must stay masked"


async def test_rooms_are_isolated_and_lobby_sees_everything(
    live_app, clock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    a1 = await make_auction(sessionmaker, now=clock.current)
    a2 = await make_auction(sessionmaker, now=clock.current)
    async with (
        ws_client(live_app) as http,
        aconnect_ws(WS, http, headers=ORIGIN) as only_a1,
        aconnect_ws(WS, http, headers=ORIGIN) as lobby,
    ):
        await subscribe(only_a1, f"auction:{a1.id}")
        await subscribe(lobby, "lobby")

        await place(http, auth(settings, user), a2.id, a2.starting_price)

        assert (await recv(lobby))["auction_id"] == str(a2.id)
        with pytest.raises(TimeoutError):
            await only_a1.receive_json(timeout=0.4)  # a2's bid must not leak into a1's room


async def test_outbid_notice_goes_only_to_the_outbid_user(
    live_app, clock, sessionmaker, settings
) -> None:
    a, b = await make_user(sessionmaker), await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    ha, hb = auth(settings, a), auth(settings, b)
    async with (
        ws_client(live_app) as http,
        aconnect_ws(WS, http, headers=ORIGIN) as ws_a,
        aconnect_ws(WS, http, headers=ORIGIN) as ws_b,
    ):
        await authenticate(ws_a, ha)
        await authenticate(ws_b, hb)

        await place(http, ha, auction.id, auction.starting_price)
        await place(http, hb, auction.id, auction.starting_price + auction.min_increment)

        notice = await recv(ws_a)
        assert notice["type"] == "outbid"
        assert notice["data"]["your_amount"] == auction.starting_price
        assert notice["data"]["current_price"] == auction.starting_price + auction.min_increment
        with pytest.raises(TimeoutError):
            await ws_b.receive_json(timeout=0.4)  # the new leader is not "outbid"


async def test_winner_is_told_privately_and_room_sees_the_close(
    live_app, clock: FakeClock, sessionmaker, settings
) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    h = auth(settings, user)
    async with ws_client(live_app) as http, aconnect_ws(WS, http, headers=ORIGIN) as ws:
        await place(http, h, auction.id, auction.starting_price)
        await authenticate(ws, h)
        await subscribe(ws, f"auction:{auction.id}")

        clock.current = auction.end_at + timedelta(seconds=1)
        await live_app.state.scheduler.tick()

        types = {(await recv(ws))["type"], (await recv(ws))["type"]}
        assert types == {"auction_ended", "auction_won"}


async def test_cannot_subscribe_to_someone_elses_private_room(live_app, sessionmaker) -> None:
    victim = await make_user(sessionmaker)
    async with ws_client(live_app) as http, aconnect_ws(WS, http, headers=ORIGIN) as ws:
        await ws.send_json({"type": "subscribe", "room": f"user:{victim.id}"})
        assert await recv(ws) == {"type": "error", "code": "BAD_ROOM"}


async def test_protocol_errors_do_not_kill_the_connection(live_app) -> None:
    async with ws_client(live_app) as http, aconnect_ws(WS, http, headers=ORIGIN) as ws:
        await ws.send_text("not json")
        assert (await recv(ws))["code"] == "BAD_MESSAGE"
        await ws.send_json({"type": "teleport"})
        assert (await recv(ws))["code"] == "UNKNOWN_TYPE"
        await ws.send_json({"type": "subscribe", "room": "auction:not-a-uuid"})
        assert (await recv(ws))["code"] == "BAD_ROOM"
        await ws.send_json({"type": "ping"})
        assert (await recv(ws)) == {"type": "pong"}


async def test_invalid_token_closes_with_4401(live_app) -> None:
    async with ws_client(live_app) as http, aconnect_ws(WS, http, headers=ORIGIN) as ws:
        await ws.send_json({"type": "auth", "token": "forged"})
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                await recv(ws)
        assert exc.value.code == 4401


async def test_foreign_origins_are_refused(live_app) -> None:
    async with ws_client(live_app) as http:
        with pytest.raises((WebSocketUpgradeError, WebSocketDisconnect)):
            async with aconnect_ws(WS, http, headers={"Origin": "https://evil.example"}) as ws:
                await recv(ws)


async def test_unsubscribe_stops_delivery(live_app, clock, sessionmaker, settings) -> None:
    user = await make_user(sessionmaker)
    auction = await make_auction(sessionmaker, now=clock.current)
    async with ws_client(live_app) as http, aconnect_ws(WS, http, headers=ORIGIN) as ws:
        room = f"auction:{auction.id}"
        await subscribe(ws, room)
        await ws.send_json({"type": "unsubscribe", "room": room})
        assert (await recv(ws))["type"] == "unsubscribed"
        await place(http, auth(settings, user), auction.id, auction.starting_price)
        with pytest.raises(TimeoutError):
            await ws.receive_json(timeout=0.4)


async def test_per_user_connection_cap(live_app, sessionmaker, settings) -> None:
    live_app.state.settings.ws_max_connections_per_user = 2
    h = auth(settings, await make_user(sessionmaker))
    try:
        async with (
            ws_client(live_app) as http,
            aconnect_ws(WS, http, headers=ORIGIN) as w1,
            aconnect_ws(WS, http, headers=ORIGIN) as w2,
            aconnect_ws(WS, http, headers=ORIGIN) as w3,
        ):
            await authenticate(w1, h)
            await authenticate(w2, h)
            await w3.send_json({"type": "auth", "token": token(h)})
            with pytest.raises(WebSocketDisconnect) as exc:
                while True:
                    await recv(w3)
            assert exc.value.code == 4429
    finally:
        live_app.state.settings.ws_max_connections_per_user = settings.ws_max_connections_per_user


async def test_connection_gauge_returns_to_zero(live_app) -> None:
    hub = live_app.state.hub
    async with ws_client(live_app) as http:
        async with aconnect_ws(WS, http, headers=ORIGIN) as ws:
            await ws.send_json({"type": "ping"})
            await recv(ws)
            assert hub.connection_count == 1
        for _ in range(100):
            if hub.connection_count == 0:
                break
            await asyncio.sleep(0.02)
    assert hub.connection_count == 0 and hub.room_count == 0
