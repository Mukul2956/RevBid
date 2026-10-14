"""Realtime event envelope and room names.

Every event carries the auction's monotonically increasing ``version``. Delivery is
at-most-once by design; clients drop events whose version is not newer than what they hold and
re-fetch the REST snapshot after any reconnect, so a lost message self-heals.

Rooms:  ``auction:{id}`` (everything about one auction) · ``lobby`` (compact list updates)
        ``user:{id}`` (private: outbid / won notices)
"""

import json
import uuid
from datetime import datetime
from typing import Any

from app.models.auction import Auction

ROOM_LOBBY = "lobby"


def room_auction(auction_id: uuid.UUID | str) -> str:
    return f"auction:{auction_id}"


def room_user(user_id: uuid.UUID | str) -> str:
    return f"user:{user_id}"


def build_event(
    type_: str, *, auction: Auction, server_time: datetime, data: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": type_,
        "auction_id": str(auction.id),
        "version": auction.version,
        "server_time": server_time.isoformat(),
        "data": data,
    }


def bike_title(auction: Auction) -> str:
    b = auction.bike
    return f"{b.year} {b.make} {b.model}"


def encode(event: dict[str, Any]) -> str:
    return json.dumps(event, separators=(",", ":"), default=str)
