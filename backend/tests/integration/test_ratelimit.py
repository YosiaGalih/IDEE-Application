"""Fixed-window rate limiter on real Redis (SECURITY_MODEL §8: 10 login attempts per minute per IP)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from app.core.ratelimit import FixedWindowLimiter, RateLimiterUnavailable
from tests.conftest import make_test_env


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(make_test_env()["REDIS_URL"])
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_allows_up_to_limit_then_blocks(redis: Redis) -> None:
    limiter = FixedWindowLimiter(redis)
    decisions = [await limiter.hit("kk:rl:test:a", limit=10, window_seconds=60) for _ in range(11)]
    assert all(d.allowed for d in decisions[:10])
    assert not decisions[10].allowed
    assert 0 < decisions[10].retry_after <= 60


async def test_keys_are_independent(redis: Redis) -> None:
    limiter = FixedWindowLimiter(redis)
    for _ in range(3):
        await limiter.hit("kk:rl:test:a", limit=3, window_seconds=60)
    assert not (await limiter.hit("kk:rl:test:a", limit=3, window_seconds=60)).allowed
    assert (await limiter.hit("kk:rl:test:b", limit=3, window_seconds=60)).allowed


async def test_unreachable_redis_raises_unavailable() -> None:
    client = Redis.from_url("redis://localhost:6390/0", socket_connect_timeout=0.5)
    try:
        with pytest.raises(RateLimiterUnavailable):
            await FixedWindowLimiter(client).hit("kk:rl:test:a", limit=1, window_seconds=60)
    finally:
        await client.aclose()
