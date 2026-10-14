"""Hub behaviour that doesn't need a network: back-pressure, bookkeeping, rate limiting."""

import asyncio
import json
import uuid

import pytest

from app.core.config import Settings
from app.core.security import Principal
from app.realtime.hub import Connection, Hub, _event_type


class FakeSocket:
    def __init__(self) -> None:
        self.closed_with: int | None = None

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def make_hub(**overrides: object) -> tuple[Hub, Settings]:
    settings = Settings(env="test", **overrides)  # type: ignore[arg-type]
    return Hub(redis=None, settings=settings), settings  # type: ignore[arg-type]


def conn(settings: Settings) -> Connection:
    return Connection(ws=FakeSocket(), settings=settings)  # type: ignore[arg-type]


async def test_slow_consumer_is_dropped_instead_of_blocking_everyone() -> None:
    hub, settings = make_hub(ws_send_queue_size=3)
    slow, healthy = conn(settings), conn(settings)
    for c in (slow, healthy):
        hub.add(c)
        hub.subscribe(c, "lobby")

    for i in range(5):
        hub._dispatch("lobby", json.dumps({"type": "x", "n": i}))
        healthy.queue.get_nowait() if healthy.queue.qsize() else None  # healthy client keeps up

    await asyncio.sleep(0)  # let the close task run
    assert slow.closing and slow.ws.closed_with == 1013  # type: ignore[attr-defined]
    assert not healthy.closing and healthy.ws.closed_with is None  # type: ignore[attr-defined]


async def test_remove_cleans_rooms_and_user_counts() -> None:
    hub, settings = make_hub()
    c = conn(settings)
    principal = Principal(id=uuid.uuid4(), role="BIDDER", display_name="x")
    hub.add(c)
    assert hub.authenticate(c, principal)
    hub.subscribe(c, f"auction:{uuid.uuid4()}")
    assert hub.room_count == 2  # private room + auction room

    hub.remove(c)
    hub.remove(c)  # idempotent

    assert hub.connection_count == 0 and hub.room_count == 0
    assert principal.id not in hub._per_user


async def test_authenticate_enforces_connection_cap_per_user() -> None:
    hub, settings = make_hub(ws_max_connections_per_user=2)
    principal = Principal(id=uuid.uuid4(), role="BIDDER", display_name="x")
    conns = [conn(settings) for _ in range(3)]
    for c in conns:
        hub.add(c)
    assert [hub.authenticate(c, principal) for c in conns] == [True, True, False]
    assert (
        hub.authenticate(conns[0], principal) is True
    )  # re-auth on same socket is not a new connection


async def test_switching_user_on_one_socket_moves_the_private_room() -> None:
    hub, settings = make_hub()
    c = conn(settings)
    hub.add(c)
    first = Principal(id=uuid.uuid4(), role="BIDDER", display_name="a")
    second = Principal(id=uuid.uuid4(), role="BIDDER", display_name="b")
    hub.authenticate(c, first)
    hub.authenticate(c, second)
    assert c.rooms == {f"user:{second.id}"}
    assert first.id not in hub._per_user


async def test_inbound_rate_limit_token_bucket() -> None:
    _, settings = make_hub(ws_burst=5, ws_messages_per_second=0.001)
    c = conn(settings)
    assert [c.allow_message() for _ in range(7)] == [True] * 5 + [False] * 2


async def test_dispatch_to_unknown_room_is_a_noop() -> None:
    hub, _ = make_hub()
    hub._dispatch("auction:nobody-listening", "{}")


def test_event_type_extraction_matches_encoder_output() -> None:
    from app.realtime.events import encode

    assert _event_type(encode({"type": "bid_placed", "x": 1})) == "bid_placed"
    assert _event_type("garbage") == "unknown"


@pytest.mark.parametrize("n", [0, 1])
async def test_resync_reaches_every_connection(n: int) -> None:
    hub, settings = make_hub()
    conns = [conn(settings) for _ in range(2 + n)]
    for c in conns:
        hub.add(c)
    hub._broadcast_resync()
    assert all(json.loads(c.queue.get_nowait()) == {"type": "resync"} for c in conns)
