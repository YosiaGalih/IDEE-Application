"""One archive import per project at a time (WP-2.1b).

A Redis key ``kk:import:<project_id>`` holds the job id from the moment an upload is accepted to the end of
its worker job. The holder extends it (``refresh``) before the job is queued and as the job runs, and only
the holder releases it. A worker that dies leaves it to expire (``LOCK_TTL_S``); the project then reads as
``import_incomplete`` and accepts a new upload, which clears what the dead job left behind.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from redis.asyncio import Redis

# One upload, or one queue wait, or one job run (its time limit is shorter); past this the holder is dead.
LOCK_TTL_S = 45 * 60

_RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


def lock_key(project_id: uuid.UUID) -> str:
    return f"kk:import:{project_id}"


async def acquire(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    return bool(await redis.set(lock_key(project_id), str(job_id), nx=True, ex=LOCK_TTL_S))


async def holder(redis: Redis, project_id: uuid.UUID) -> str | None:
    value = await redis.get(lock_key(project_id))
    return value.decode() if isinstance(value, bytes) else value


async def release(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """Delete the lock only if ``job_id`` still holds it (a late job never frees a newer import's lock)."""
    released = await redis.eval(_RELEASE, 1, lock_key(project_id), str(job_id))  # type: ignore[misc]
    return bool(released)


async def running(redis: Redis, project_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The projects among ``project_ids`` with an import in progress (one round trip)."""
    ids = list(project_ids)
    if not ids:
        return set()
    async with redis.pipeline(transaction=False) as pipe:
        for project_id in ids:
            pipe.exists(lock_key(project_id))
        held = await pipe.execute()
    return {project_id for project_id, count in zip(ids, held, strict=True) if count}


_REFRESH = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('EXPIRE', KEYS[1], ARGV[2]) end return 0"
)


async def refresh(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """Extend the lock if ``job_id`` still holds it: True, or False when it was lost (expired, or a newer
    upload took it). Called while the upload streams, before the job is queued, and as the job runs."""
    extended = await redis.eval(_REFRESH, 1, lock_key(project_id), str(job_id), str(LOCK_TTL_S))  # type: ignore[misc]
    return bool(extended)
