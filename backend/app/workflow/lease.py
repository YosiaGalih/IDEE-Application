"""Task lease (ARCHITECTURE §5.3, NFR-05): at most one worker executes a task at a time.

All functions run in the caller's transaction; the caller commits. Times use ``clock_timestamp()`` so a
transaction that waited for the claim lock does not hand out an already-shortened lease.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Task

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 20
_LEASE = timedelta(seconds=LEASE_SECONDS)
_WORKER_ID_LENGTH = 120  # tasks.lease_owner
# Serialises claims so the MAX_ACTIVE_TASKS count and the claim cannot interleave.
_CLAIM_LOCK = "kodekantor.claim_lease"


def _check_worker(worker_id: str) -> None:
    if not 1 <= len(worker_id) <= _WORKER_ID_LENGTH:
        raise ValueError(f"worker id must be 1-{_WORKER_ID_LENGTH} characters")


async def claim_lease(db: AsyncSession, task_id: uuid.UUID, worker_id: str, *, max_active: int) -> bool:
    """Take the task's lease unless another worker holds a live one or ``max_active`` leases are live."""
    _check_worker(worker_id)
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(_CLAIM_LOCK))))
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
                or_(Task.lease_expires_at.is_(None), Task.lease_expires_at < now),
                live_elsewhere < max_active,
            )
            .values(lease_owner=worker_id, lease_expires_at=now + _LEASE)
            .returning(Task.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    return claimed is not None


async def renew_lease(db: AsyncSession, task_id: uuid.UUID, worker_id: str) -> bool:
    """Heartbeat. False means the lease is lost and the worker must stop working on the task."""
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
    return renewed is not None


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
    return released is not None
