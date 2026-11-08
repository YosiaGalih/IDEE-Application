"""One provider smoke test at a time across every process (spec 3.1e Goal 4), the shape of ``index_lock``.

A Redis key ``kk:smoke:<provider>`` holds the job id from the request (API) or the start (CLI) to the end
of the run. Only the holder releases it; a worker that dies leaves it to expire (``LOCK_TTL_S``). No key is
read here, so the ``api`` process may import it.
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

# A queue wait plus one run (time limit provider_smoke.SMOKE_TIME_LIMIT_S, 3 min): past this the holder is
# dead.
LOCK_TTL_S = 5 * 60

_RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


def lock_key(provider: str) -> str:
    return f"kk:smoke:{provider}"


async def acquire(redis: Redis, provider: str, job_id: uuid.UUID) -> bool:
    return bool(await redis.set(lock_key(provider), str(job_id), nx=True, ex=LOCK_TTL_S))


_CLAIM = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], ARGV[2]) end return 0"
)


async def claim(redis: Redis, provider: str, job_id: uuid.UUID) -> bool:
    """The job starts: true only while ``job_id`` holds the lock, which then gets a full TTL again (a long
    queue wait must not let it expire mid-run; silent-failure 3.1e1 M5)."""
    claimed = await redis.eval(_CLAIM, 1, lock_key(provider), str(job_id), str(LOCK_TTL_S))  # type: ignore[misc]
    return bool(claimed)


async def holder(redis: Redis, provider: str) -> str | None:
    value = await redis.get(lock_key(provider))
    return value.decode() if isinstance(value, bytes) else value


async def release(redis: Redis, provider: str, job_id: uuid.UUID) -> bool:
    """Delete the lock only if ``job_id`` still holds it (a late run never frees a newer one's lock)."""
    released = await redis.eval(_RELEASE, 1, lock_key(provider), str(job_id))  # type: ignore[misc]
    return bool(released)
