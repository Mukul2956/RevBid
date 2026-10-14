"""Fixed-window rate limiter on Redis.

Fails *open*: if Redis is unavailable we log, count it, and let the request through. Availability
of bidding matters more than strict throttling, and auth endpoints are additionally protected by
argon2's cost. Each rule has its own metric so rejections are visible on the dashboard.
"""

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core import metrics
from app.domain.errors import RateLimited

log = structlog.stdlib.get_logger("app.rate_limit")


class RateLimiter:
    def __init__(self, redis: Redis, enabled: bool = True) -> None:
        self._redis = redis
        self._enabled = enabled

    async def hit(self, rule: str, key: str, *, limit: int, window_s: int) -> None:
        """Count one event; raise ``RateLimited`` once ``limit`` is exceeded inside the window."""
        if not self._enabled:
            return
        redis_key = f"rl:{rule}:{key}"
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.set(redis_key, 0, ex=window_s, nx=True)  # start window on first hit only
                pipe.incr(redis_key)
                pipe.ttl(redis_key)
                _, count, ttl = await pipe.execute()
        except (RedisError, OSError):
            metrics.RATE_LIMIT_ERRORS.inc()
            log.warning("rate_limiter_unavailable", rule=rule, exc_info=True)
            return
        if count > limit:
            metrics.RATE_LIMITED.labels(rule).inc()
            raise RateLimited(
                "Too many requests, please slow down", retry_after=max(int(ttl), 1), rule=rule
            )
