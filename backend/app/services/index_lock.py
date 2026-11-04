"""One index job per project at a time (WP-2.1d), the same shape as ``import_lock``.

A Redis key ``kk:index:<project_id>`` holds the job id from the request to the end of its worker job.
- The job extends it at the start of each round (``begin_round``); a job whose request was superseded finds
  it gone or taken. Only the holder releases it.
- A request that finds a job running asks it for one more round (``reuse``); the job takes it when it ends a
  round (``finish``).
- A worker that dies leaves the lock to expire (``LOCK_TTL_S``); the next request then queues a new job.
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

# A queue wait plus one job run (time limit 20 min): past this the holder is dead.
LOCK_TTL_S = 30 * 60

_RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"


# A request that finds a job holding the lock asks it for one more round: that job may have read HEAD
# before the commit the request is about (backend review S4). Both steps are atomic with the lock.
_REUSE = (
    "local held = redis.call('GET', KEYS[1]) "
    "if held then redis.call('SET', KEYS[2], '1', 'EX', ARGV[1]) end return held"
)
# A round starts: the lock is extended and earlier "again" asks are dropped, since this round reads HEAD
# after them. Only asks that arrive once the round runs earn another one.
_BEGIN = (
    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end "
    "redis.call('DEL', KEYS[2]) redis.call('EXPIRE', KEYS[1], ARGV[2]) return 1"
)
_FINISH = (
    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end "
    "if redis.call('DEL', KEYS[2]) == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) return 2 end "
    "redis.call('DEL', KEYS[1]) return 1"
)


def lock_key(project_id: uuid.UUID) -> str:
    return f"kk:index:{project_id}"


def again_key(project_id: uuid.UUID) -> str:
    return f"kk:index:{project_id}:again"


async def acquire(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    return bool(await redis.set(lock_key(project_id), str(job_id), nx=True, ex=LOCK_TTL_S))


async def holder(redis: Redis, project_id: uuid.UUID) -> str | None:
    value = await redis.get(lock_key(project_id))
    return value.decode() if isinstance(value, bytes) else value


async def release(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """Delete the lock only if ``job_id`` still holds it (a late job never frees a newer request's lock)."""
    released = await redis.eval(_RELEASE, 1, lock_key(project_id), str(job_id))  # type: ignore[misc]
    return bool(released)


async def reuse(redis: Redis, project_id: uuid.UUID) -> str | None:
    """The job id holding the lock, after asking it for another round; ``None`` when no job holds it."""
    held = await redis.eval(_REUSE, 2, lock_key(project_id), again_key(project_id), str(LOCK_TTL_S))  # type: ignore[misc]
    return held.decode() if isinstance(held, bytes) else held


async def finish(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """End a round: ``True`` when a request asked for another one (the lock stays, extended), ``False`` when
    the lock was released (or was not ``job_id``'s any more)."""
    done = await redis.eval(  # type: ignore[misc]
        _FINISH, 2, lock_key(project_id), again_key(project_id), str(job_id), str(LOCK_TTL_S)
    )
    return int(done) == 2


async def begin_round(redis: Redis, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
    """Start a round if ``job_id`` still holds the lock (False: expired or taken by a newer request)."""
    began = await redis.eval(  # type: ignore[misc]
        _BEGIN, 2, lock_key(project_id), again_key(project_id), str(job_id), str(LOCK_TTL_S)
    )
    return bool(began)
