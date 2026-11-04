"""One index job per project at a time (WP-2.1d), the same shape as ``import_lock``.

A Redis key ``kk:index:<project_id>`` holds the job id from the request to the end of its worker job. The
job extends it when it starts (``refresh``: a job whose request was superseded finds it gone or taken) and
releases it at the end; only the holder may. A worker that dies leaves it to expire (``LOCK_TTL_S``), after
which the next request queues a new job.
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

# A queue wait plus one job run (time limit 20 min): past this the holder is dead.
LOCK_TTL_S = 30 * 60

_RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
_REFRESH = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], ARGV[2]) end return 0"
)


def lock_key(project_id: uuid.UUID) -> str:
    return f"kk:index:{project_id}"


async def acquire(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    return bool(await redis.set(lock_key(project_id), str(job_id), nx=True, ex=LOCK_TTL_S))


async def holder(redis: Redis, project_id: uuid.UUID) -> str | None:
    value = await redis.get(lock_key(project_id))
    return value.decode() if isinstance(value, bytes) else value


async def refresh(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """Extend the lock if ``job_id`` still holds it; False when it expired or a newer request took it."""
    refreshed = await redis.eval(_REFRESH, 1, lock_key(project_id), str(job_id), str(LOCK_TTL_S))  # type: ignore[misc]
    return bool(refreshed)


async def release(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """Delete the lock only if ``job_id`` still holds it (a late job never frees a newer request's lock)."""
    released = await redis.eval(_RELEASE, 1, lock_key(project_id), str(job_id))  # type: ignore[misc]
    return bool(released)
