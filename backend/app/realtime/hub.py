"""WebSocket fan-out hub.

Topology
--------
Each API process keeps in-memory *rooms* (room name -> local connections) and holds exactly one
Redis pattern subscription (``rt:*``). A publish from any instance reaches every instance via
Redis, and each instance forwards it to its own subscribed sockets. That is what makes the API
horizontally scalable without sticky sessions.

Back-pressure
-------------
The Redis listener must never block on a slow client. Every connection owns a bounded queue and a
dedicated writer task; if a queue fills up, that connection is dropped (the client reconnects and
re-syncs from REST) instead of stalling everyone else.
"""

import asyncio
import contextlib
import json
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import WebSocket
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core import metrics
from app.core.config import Settings
from app.core.security import Principal
from app.core.shutdown import run_bounded
from app.realtime.events import room_user
from app.realtime.publisher import CHANNEL_PREFIX

log = structlog.stdlib.get_logger("app.realtime.hub")

CLOSE_GOING_AWAY = 1001
CLOSE_TRY_AGAIN_LATER = 1013
CLOSE_POLICY = 1008
POLL_TIMEOUT_S = 1.0


@dataclass(eq=False)
class Connection:
    ws: WebSocket
    settings: Settings
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    principal: Principal | None = None
    rooms: set[str] = field(default_factory=set)
    queue: asyncio.Queue[str] = field(init=False)
    closing: bool = False
    _tokens: float = field(init=False)
    _refilled_at: float = field(init=False, default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=self.settings.ws_send_queue_size)
        self._tokens = float(self.settings.ws_burst)

    def allow_message(self) -> bool:
        """Token bucket on inbound client messages."""
        now = time.monotonic()
        self._tokens = min(
            float(self.settings.ws_burst),
            self._tokens + (now - self._refilled_at) * self.settings.ws_messages_per_second,
        )
        self._refilled_at = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


def _event_type(payload: str) -> str:
    """Cheap label extraction: events are encoded with ``type`` as the first key."""
    start = payload.find('"type":"')
    if start < 0:
        return "unknown"
    start += 8
    return payload[start : payload.find('"', start)]


class Hub:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings
        self._rooms: dict[str, set[Connection]] = defaultdict(set)
        self._connections: set[Connection] = set()
        self._per_user: Counter[uuid.UUID] = Counter()
        self._listener: asyncio.Task[None] | None = None
        self._stopping = False
        # Set while the Redis subscription is live; lets callers (tests, health) wait for it.
        self.ready = asyncio.Event()
        self._bg: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self._listener = asyncio.create_task(self._listen(), name="hub-redis-listener")

    async def stop(self) -> None:
        self._stopping = True
        if self._listener:
            self._listener.cancel()
            await run_bounded(self._listener, budget_s=3.0, step="hub_listener")
        for conn in list(self._connections):
            self.drop(conn, "shutdown", code=CLOSE_GOING_AWAY)
        if self._bg:
            await run_bounded(
                asyncio.gather(*self._bg, return_exceptions=True),
                budget_s=3.0,
                step="hub_close_sockets",
            )

    # ------------------------------------------------------------------ introspection
    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def room_count(self) -> int:
        return sum(1 for members in self._rooms.values() if members)

    # ------------------------------------------------------------------ connections
    def add(self, conn: Connection) -> None:
        self._connections.add(conn)
        metrics.WS_CONNECTIONS.inc()

    def remove(self, conn: Connection) -> None:
        if conn not in self._connections:
            return
        self._connections.discard(conn)
        metrics.WS_CONNECTIONS.dec()
        for room in list(conn.rooms):
            self._leave(conn, room)
        if conn.principal:
            self._release_user(conn.principal.id)

    def _release_user(self, user_id: uuid.UUID) -> None:
        self._per_user[user_id] -= 1
        if self._per_user[user_id] <= 0:
            del self._per_user[user_id]

    def authenticate(self, conn: Connection, principal: Principal) -> bool:
        """Bind a user to the connection. False if the per-user connection cap is exceeded."""
        if conn.principal and conn.principal.id == principal.id:
            conn.principal = principal  # token refresh on the same socket
            return True
        if self._per_user[principal.id] >= self._settings.ws_max_connections_per_user:
            return False
        if conn.principal:
            self._release_user(conn.principal.id)
            self._leave(conn, room_user(conn.principal.id))
        conn.principal = principal
        self._per_user[principal.id] += 1
        self.subscribe(conn, room_user(principal.id))
        return True

    def drop(self, conn: Connection, reason: str, *, code: int = CLOSE_TRY_AGAIN_LATER) -> None:
        if conn.closing:
            return
        conn.closing = True
        metrics.WS_DROPPED.labels(reason).inc()
        log.info("ws_dropped", conn=conn.id, reason=reason)
        task = asyncio.create_task(self._close_quietly(conn.ws, code))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    @staticmethod
    async def _close_quietly(ws: WebSocket, code: int) -> None:
        with contextlib.suppress(Exception):
            await ws.close(code=code)

    # ------------------------------------------------------------------ rooms
    def subscribe(self, conn: Connection, room: str) -> None:
        conn.rooms.add(room)
        self._rooms[room].add(conn)

    def unsubscribe(self, conn: Connection, room: str) -> None:
        self._leave(conn, room)

    def _leave(self, conn: Connection, room: str) -> None:
        conn.rooms.discard(room)
        members = self._rooms.get(room)
        if members is not None:
            members.discard(conn)
            if not members:
                del self._rooms[room]

    # ------------------------------------------------------------------ delivery
    def send(self, conn: Connection, message: dict[str, Any]) -> None:
        self._enqueue(conn, json.dumps(message, separators=(",", ":")))

    def _enqueue(self, conn: Connection, payload: str) -> None:
        if conn.closing:
            return
        try:
            conn.queue.put_nowait(payload)
        except asyncio.QueueFull:
            self.drop(conn, "slow_consumer")

    def _dispatch(self, room: str, payload: str) -> None:
        for conn in tuple(self._rooms.get(room, ())):
            self._enqueue(conn, payload)

    def _broadcast_resync(self) -> None:
        payload = json.dumps({"type": "resync"}, separators=(",", ":"))
        for conn in tuple(self._connections):
            self._enqueue(conn, payload)

    async def writer(self, conn: Connection) -> None:
        """Single writer per socket: serialises sends and applies back-pressure."""
        try:
            while True:
                payload = await conn.queue.get()
                await conn.ws.send_text(payload)
                metrics.WS_MESSAGES_SENT.labels(_event_type(payload)).inc()
        except asyncio.CancelledError:
            raise
        except Exception:  # socket gone mid-send; the receive loop performs cleanup
            log.debug("ws_writer_stopped", conn=conn.id, exc_info=True)

    # ------------------------------------------------------------------ redis listener
    async def _listen(self) -> None:
        backoff = 0.5
        first = True
        while True:
            pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            try:
                await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
                if not first:
                    # Messages may have been missed while disconnected: tell clients to re-sync.
                    metrics.REDIS_LISTENER_RECONNECTS.inc()
                    log.warning("hub_redis_reconnected")
                    self._broadcast_resync()
                first = False
                backoff = 0.5
                self.ready.set()
                log.info("hub_listening")
                # Poll with a short timeout rather than blocking in `listen()`: it bounds how
                # long shutdown (or a half-open socket) can go unnoticed to ~one poll interval.
                while not self._stopping:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=POLL_TIMEOUT_S
                    )
                    if message is None or message.get("type") != "pmessage":
                        continue
                    channel: str = message["channel"]
                    self._dispatch(channel[len(CHANNEL_PREFIX) :], message["data"])
                return
            except asyncio.CancelledError:
                raise
            except (RedisError, OSError):
                log.warning("hub_redis_error", retry_in=backoff, exc_info=True)
            finally:
                self.ready.clear()
                await run_bounded(
                    pubsub.aclose(),  # type: ignore[no-untyped-call]
                    budget_s=2.0,
                    step="hub_pubsub_close",
                )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10.0)
