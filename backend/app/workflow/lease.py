"""Task lease (ARCHITECTURE §5.3, NFR-05): at most one worker executes a task at a time.

All functions run in the caller's transaction; the caller commits right away (the claim lock is held until
then). Times use ``clock_timestamp()`` so a transaction that waited for the claim lock does not hand out an
already-shortened lease.

Contract for the job that runs a task (WP-3.x):
- ``claim_lease`` returns *why* it did not acquire:
  - ``AT_CAPACITY`` and ``BUSY`` mean "retry later";
  - ``HELD``, ``NOT_CLAIMABLE`` and ``NOT_FOUND`` mean "this delivery has nothing to do".
- Only ``queued`` tasks are claimable, so a late duplicate delivery never re-runs a finished task.
- ``renew_lease`` raises ``LeaseLost``. A worker that loses its lease must stop; the recovery sweep owns the
  task from then on.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.tasks import Task

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 20
CLAIM_LOCK_TIMEOUT = "5s"
_LEASE = timedelta(seconds=LEASE_SECONDS)
_WORKER_ID_LENGTH = 120  # tasks.lease_owner
# Serialises claims so the MAX_ACTIVE_TASKS count and the claim cannot interleave.
_CLAIM_LOCK = "kodekantor.claim_lease"
_LOCK_NOT_AVAILABLE = "55P03"

logger = get_logger(__name__)


class ClaimResult(StrEnum):
    ACQUIRED = "acquired"
    HELD = "held"  # another worker holds a live lease
    AT_CAPACITY = "at_capacity"  # MAX_ACTIVE_TASKS live leases elsewhere: retry later
    BUSY = "busy"  # the claim lock was not available in time: retry later
    NOT_CLAIMABLE = "not_claimable"  # not queued (finished, running, ...): nothing to do
    NOT_FOUND = "not_found"


class LeaseLost(Exception):
    """The worker no longer holds the task's lease and must stop working on it."""


def _check_worker(worker_id: str) -> None:
    if not 1 <= len(worker_id) <= _WORKER_ID_LENGTH:
        raise ValueError(f"worker id must be 1-{_WORKER_ID_LENGTH} characters")


async def claim_lease(
    db: AsyncSession, task_id: uuid.UUID, worker_id: str, *, max_active: int
) -> ClaimResult:
    """Take the lease of a queued task unless it is held or ``max_active`` leases are live elsewhere."""
    _check_worker(worker_id)
    try:
        async with db.begin_nested():
            previous = await db.scalar(select(func.current_setting("lock_timeout")))
            await db.execute(select(func.set_config("lock_timeout", CLAIM_LOCK_TIMEOUT, True)))
            await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(_CLAIM_LOCK))))
            await db.execute(select(func.set_config("lock_timeout", previous, True)))
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE or "lock timeout" in str(exc.orig):
            return ClaimResult.BUSY
        raise

    now = func.clock_timestamp()
    live_elsewhere = (
        select(func.count())
        .select_from(Task)
        .where(Task.lease_expires_at > now, Task.id != task_id)
        .scalar_subquery()
    )
    claimed = (
        await db.execute(
            update(Task)
            .where(
                Task.id == task_id,
                Task.status == "queued",
                or_(Task.lease_expires_at.is_(None), Task.lease_expires_at < now),
                live_elsewhere < max_active,
            )
            .values(lease_owner=worker_id, lease_expires_at=now + _LEASE)
            .returning(Task.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if claimed is not None:
        return ClaimResult.ACQUIRED

    row = (
        await db.execute(
            select(Task.status, (Task.lease_expires_at > func.clock_timestamp()).label("live")).where(
                Task.id == task_id
            )
        )
    ).one_or_none()
    if row is None:
        return ClaimResult.NOT_FOUND
    if row.status != "queued":
        return ClaimResult.NOT_CLAIMABLE
    if row.live:
        return ClaimResult.HELD
    return ClaimResult.AT_CAPACITY


async def renew_lease(db: AsyncSession, task_id: uuid.UUID, worker_id: str) -> None:
    """Heartbeat: extend the lease, or raise ``LeaseLost`` if this worker no longer holds a live one."""
    _check_worker(worker_id)
    now = func.clock_timestamp()
    renewed = (
        await db.execute(
            update(Task)
            .where(Task.id == task_id, Task.lease_owner == worker_id, Task.lease_expires_at > now)
            .values(lease_expires_at=now + _LEASE)
            .returning(Task.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if renewed is None:
        raise LeaseLost(f"worker {worker_id} lost the lease of task {task_id}")


async def release_lease(db: AsyncSession, task_id: uuid.UUID, worker_id: str) -> bool:
    _check_worker(worker_id)
    released = (
        await db.execute(
            update(Task)
            .where(Task.id == task_id, Task.lease_owner == worker_id)
            .values(lease_owner=None, lease_expires_at=None)
            .returning(Task.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if released is None:
        logger.warning("lease_release_not_owner", task_id=str(task_id), worker_id=worker_id)
    return released is not None
