"""Fixed-window rate limiting in Redis (SECURITY_MODEL §8: login 10/minute/IP).

Callers fail closed on ``RateLimiterUnavailable``: an unenforced limit is not a limit.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError


class RateLimiterUnavailable(Exception):
    pass


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after: int


class FixedWindowLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> RateDecision:
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, window_seconds, nx=True)
                pipe.ttl(key)
                count, _, ttl = await pipe.execute()
        except RedisError:
            # The client error can carry the Redis URL; callers only need to know it failed.
            raise RateLimiterUnavailable("rate limiter backend is unavailable") from None
        retry_after = ttl if ttl > 0 else window_seconds
        return RateDecision(allowed=count <= limit, retry_after=retry_after)
