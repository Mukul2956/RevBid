"""The ``/ws`` endpoint.

Protocol (JSON text frames)::

    client -> {"type":"auth","token":"<access jwt>"}             (optional; may be repeated)
    client -> {"type":"subscribe","room":"auction:<uuid>"|"lobby"}
    client -> {"type":"unsubscribe","room":"..."}   client -> {"type":"ping"}
    server -> {"type":"authed"|"subscribed"|"unsubscribed"|"pong"|"error", ...}
    server -> events (see events.py) and {"type":"resync"} after a server-side gap

Anonymous visitors may watch public rooms; ``auth`` unlocks the private ``user:{id}`` room
(outbid / won notices). Bids themselves go through the REST API, never over this socket.
"""

import asyncio
import contextlib
import json
import uuid
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import decode_access_token
from app.domain.errors import InvalidToken
from app.realtime.events import ROOM_LOBBY
from app.realtime.hub import CLOSE_POLICY, Connection, Hub

log = structlog.stdlib.get_logger("app.realtime.ws")

router = APIRouter()

MAX_MESSAGE_BYTES = 4096
CLOSE_UNAUTHORIZED = 4401
CLOSE_TOO_MANY = 4429


def _valid_room(room: Any) -> bool:
    if room == ROOM_LOBBY:
        return True
    if isinstance(room, str) and room.startswith("auction:"):
        try:
            uuid.UUID(room.removeprefix("auction:"))
        except ValueError:
            return False
        return True
    return False


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    hub: Hub = websocket.app.state.hub

    # Browsers always send Origin; reject cross-site pages. Non-browser clients omit it.
    origin = websocket.headers.get("origin")
    if origin and origin not in settings.ws_allowed_origins:
        log.warning("ws_origin_rejected", origin=origin)
        await websocket.close(code=CLOSE_POLICY)
        return

    await websocket.accept()
    conn = Connection(ws=websocket, settings=settings)
    hub.add(conn)
    writer = asyncio.create_task(hub.writer(conn), name=f"ws-writer-{conn.id}")
    log.info("ws_connected", conn=conn.id)
    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw) > MAX_MESSAGE_BYTES:
                hub.drop(conn, "oversized_message", code=1009)
                break
            if not conn.allow_message():
                hub.send(conn, {"type": "error", "code": "RATE_LIMITED"})
                continue
            if not await _handle(hub, conn, raw):
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws_error", conn=conn.id)
    finally:
        hub.remove(conn)
        writer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer
        log.info("ws_disconnected", conn=conn.id)


async def _handle(hub: Hub, conn: Connection, raw: str) -> bool:
    """Process one client frame. Returns False if the connection should be closed."""
    try:
        msg = json.loads(raw)
        kind = msg["type"]
    except (ValueError, KeyError, TypeError):
        hub.send(conn, {"type": "error", "code": "BAD_MESSAGE"})
        return True

    if kind == "ping":
        hub.send(conn, {"type": "pong"})
    elif kind == "auth":
        try:
            principal = decode_access_token(conn.settings, str(msg.get("token", "")))
        except InvalidToken:
            hub.send(conn, {"type": "error", "code": "INVALID_TOKEN"})
            hub.drop(conn, "invalid_token", code=CLOSE_UNAUTHORIZED)
            return False
        if not hub.authenticate(conn, principal):
            hub.send(conn, {"type": "error", "code": "TOO_MANY_CONNECTIONS"})
            hub.drop(conn, "too_many_connections", code=CLOSE_TOO_MANY)
            return False
        hub.send(conn, {"type": "authed", "user_id": str(principal.id)})
    elif kind in ("subscribe", "unsubscribe"):
        room = msg.get("room")
        if not _valid_room(room):
            hub.send(conn, {"type": "error", "code": "BAD_ROOM"})
        elif kind == "subscribe":
            if len(conn.rooms) >= conn.settings.ws_max_subscriptions:
                hub.send(conn, {"type": "error", "code": "TOO_MANY_SUBSCRIPTIONS"})
            else:
                hub.subscribe(conn, room)
                hub.send(conn, {"type": "subscribed", "room": room})
        else:
            hub.unsubscribe(conn, room)
            hub.send(conn, {"type": "unsubscribed", "room": room})
    else:
        hub.send(conn, {"type": "error", "code": "UNKNOWN_TYPE"})
    return True
