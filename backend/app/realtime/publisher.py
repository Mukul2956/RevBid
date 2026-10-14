"""Publishing realtime events.

Publishing happens strictly *after* the database transaction commits and is best-effort: the
bid is already durable, so a Redis hiccup must never fail (or roll back) a bid. Clients heal
by re-syncing from REST.
"""

from typing import Any, Protocol

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core import metrics
from app.realtime.events import encode

log = structlog.stdlib.get_logger("app.realtime.publisher")

CHANNEL_PREFIX = "rt:"


class Publisher(Protocol):
    async def publish_many(self, items: list[tuple[str, dict[str, Any]]]) -> None: ...


class RedisPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish_many(self, items: list[tuple[str, dict[str, Any]]]) -> None:
        if not items:
            return
        try:
            async with self._redis.pipeline(transaction=False) as pipe:
                for room, event in items:
                    pipe.publish(f"{CHANNEL_PREFIX}{room}", encode(event))
                await pipe.execute()
        except (RedisError, OSError):
            metrics.PUBLISH_FAILURES.inc()
            log.warning("realtime_publish_failed", rooms=[r for r, _ in items], exc_info=True)


class RecordingPublisher:
    """Test double that records what would have been published."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def publish_many(self, items: list[tuple[str, dict[str, Any]]]) -> None:
        self.events.extend(items)

    def of_type(self, type_: str) -> list[tuple[str, dict[str, Any]]]:
        return [(r, e) for r, e in self.events if e["type"] == type_]
