"""Task lease (ARCHITECTURE §5.3, NFR-05): at most one worker executes a task at a time.

All functions run in the caller's transaction; the caller commits right away (the claim lock is held until
then). Times use ``clock_timestamp()`` so a transaction that waited for the claim lock does not hand out an
already-shortened lease.

Contract for the job that runs a task (WP-3.x):
- ``claim_lease`` returns *why* it did not acquire:
  - ``AT_CAPACITY`` and ``BUSY`` mean "retry later";
  - ``HELD``, ``NOT_CLAIMABLE`` and ``NOT_FOUND`` mean "this delivery has nothing to do".
- Only ``queued`` tasks are claimable by default (a caller may pass ``claimable``), so a late duplicate
  delivery never re-runs a finished task.
- ``renew_lease`` raises ``LeaseLost``. A worker that loses its lease must stop; the recovery sweep owns the
  task from then on.
- ``revoke_lease_owner`` makes every later ``renew_lease`` of that owner, in this process, raise
  ``LeaseLost`` without SQL (spec §16.6.2 #84). The engine revokes its delivery's owner when a step hangs in
  its cleanup past the grace, or when its heartbeat gives up. A renewal already past the gate can still land
  up to the lock/statement timeout later (on the engine path at most ``ENGINE_WRITE_TIMEOUT_S``, 15 s), so a
  lease ends at most revocation + 15 s + ``LEASE_SECONDS``.
- ``claim_lease`` returns ``HELD`` for a revoked owner, without SQL: an owner is unique per delivery and the
  engine claims before it ever revokes, so a legitimate claim never meets a revoked owner. ``release_lease``
  is not gated (it frees only a lease the owner still holds).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import Row, func, or_, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.tasks import Task

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 20
# The heartbeat gives up this long before the lease ends: a hung renewal is cut off and the step cancelled
# while the lease is still ours. A cancelled asyncpg connection can overrun its bound by 2 s (§16.5a #1).
LEASE_SAFETY_SECONDS = 10
CLAIM_LOCK_TIMEOUT = "5s"
_LEASE = timedelta(seconds=LEASE_SECONDS)
LEASE_OWNER_LENGTH = 120  # tasks.lease_owner
# Owners the engine gave up on in this process; see ``revoke_lease_owner``. Never emptied by app code.
_REVOKED_OWNERS: set[str] = set()
# Serialises claims so the MAX_ACTIVE_TASKS count and the claim cannot interleave.
_CLAIM_LOCK = "kodekantor.claim_lease"
_LOCK_NOT_AVAILABLE = "55P03"

logger = get_logger(__name__)


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """55P03 (lock_not_available). A statement timeout (57014) and every other error are not."""
    return getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE


_DEFAULT_CLAIMABLE = frozenset({"queued"})


class ClaimResult(StrEnum):
    ACQUIRED = "acquired"
    HELD = "held"  # another worker holds a live lease
    AT_CAPACITY = "at_capacity"  # MAX_ACTIVE_TASKS live leases elsewhere: retry later
    # The claim lock was not available in time: retry later. The advisory lock (``pg_advisory_xact_lock``) may
    # still be held after BUSY, so the caller must end its transaction (commit or rollback) promptly.
    BUSY = "busy"
    NOT_CLAIMABLE = "not_claimable"  # not queued (finished, running, ...): nothing to do
    NOT_FOUND = "not_found"


class LeaseLost(Exception):
    """The worker no longer holds the task's lease and must stop working on it."""


def lease_duration() -> timedelta:
    """The lease length ``claim_lease`` and ``renew_lease`` grant; a holder's own deadlines derive from it."""
    return _LEASE


def _check_worker(worker_id: str) -> None:
    if not 1 <= len(worker_id) <= LEASE_OWNER_LENGTH:
        raise ValueError(f"worker id must be 1-{LEASE_OWNER_LENGTH} characters")


def revoke_lease_owner(owner: str) -> None:
    """Refuse every later ``renew_lease`` of ``owner`` in this process (``LeaseLost``, no SQL).

    The set is module-private and process-local, and app code never empties it:

    - one delivery runs per prefork child (``worker_prefetch_multiplier=1``, one ``asyncio.run`` per
      delivery), and its owner is unique to the delivery (#80), so a revoked owner never serves a later one;
    - it grows by one entry per revocation, which is accepted (an overrun or a give-up is rare, and a child
      is recycled);
    - a renewal already past this gate can still land, up to the lock/statement timeout later (on the engine
      path at most ``ENGINE_WRITE_TIMEOUT_S``, 15 s); the lease then ends at most 15 s + one lease period
      (``LEASE_SECONDS``) after the revocation.

    Tests clear the set with a fixture (``tests/conftest.py``).
    """
    _check_worker(owner)
    _REVOKED_OWNERS.add(owner)


async def claim_lease(
    db: AsyncSession,
    task_id: uuid.UUID,
    worker_id: str,
    *,
    max_active: int,
    claimable: frozenset[str] = _DEFAULT_CLAIMABLE,
) -> ClaimResult:
    """Take the lease of a task in a ``claimable`` status (default: queued).

    Refused when another worker holds it or ``max_active`` leases are live elsewhere, and for a revoked owner
    (``HELD``, no SQL).
    """
    _check_worker(worker_id)
    if worker_id in _REVOKED_OWNERS:
        return ClaimResult.HELD
    try:
        async with db.begin_nested():
            previous = await db.scalar(select(func.current_setting("lock_timeout")))
            await db.execute(select(func.set_config("lock_timeout", CLAIM_LOCK_TIMEOUT, True)))
            await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(_CLAIM_LOCK))))
            await db.execute(select(func.set_config("lock_timeout", previous, True)))
    except DBAPIError as exc:
        if _is_lock_timeout(exc):
            return ClaimResult.BUSY
        raise

    now = func.clock_timestamp()
    live_elsewhere = (
        select(func.count())
        .select_from(Task)
        .where(Task.lease_expires_at > now, Task.id != task_id)
        .scalar_subquery()
    )
    try:
        # A savepoint: a lock timeout on a row held elsewhere must not abort the caller's transaction.
        async with db.begin_nested():
            claimed = (
                await db.execute(
                    update(Task)
                    .where(
                        Task.id == task_id,
                        Task.status.in_(claimable),
                        or_(Task.lease_expires_at.is_(None), Task.lease_expires_at < now),
                        live_elsewhere < max_active,
                    )
                    .values(lease_owner=worker_id, lease_expires_at=now + _LEASE)
                    .returning(Task.id)
                    .execution_options(synchronize_session=False)
                )
            ).scalar_one_or_none()
    except DBAPIError as exc:
        if _is_lock_timeout(exc):
            return ClaimResult.BUSY
        raise
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
    if row.status not in claimable:
        return ClaimResult.NOT_CLAIMABLE
    if row.live:
        return ClaimResult.HELD
    return ClaimResult.AT_CAPACITY


@dataclass(frozen=True)
class LeaseFlags:
    """The task's stop requests, read in the same statement that renews its lease."""

    cancel_requested: bool
    pause_requested: bool


async def _renew(db: AsyncSession, task_id: uuid.UUID, worker_id: str) -> Row[bool, bool]:
    """The one renewal statement and its gate (``renew_lease`` and ``renew_lease_flags`` share it)."""
    _check_worker(worker_id)
    if worker_id in _REVOKED_OWNERS:
        raise LeaseLost(f"the lease owner of task {task_id} was revoked in this process")
    now = func.clock_timestamp()
    renewed = (
        await db.execute(
            update(Task)
            .where(Task.id == task_id, Task.lease_owner == worker_id, Task.lease_expires_at > now)
            .values(lease_expires_at=now + _LEASE)
            .returning(Task.cancel_requested, Task.pause_requested)
            .execution_options(synchronize_session=False)
        )
    ).one_or_none()
    if renewed is None:
        raise LeaseLost(f"the lease of task {task_id} was lost")
    return renewed


async def renew_lease(db: AsyncSession, task_id: uuid.UUID, worker_id: str) -> None:
    """Heartbeat: extend the lease, or raise ``LeaseLost`` if this worker no longer holds a live one."""
    await _renew(db, task_id, worker_id)


async def renew_lease_flags(db: AsyncSession, task_id: uuid.UUID, worker_id: str) -> LeaseFlags:
    """``renew_lease`` that also returns the cancel and pause flags; it raises ``LeaseLost`` exactly when
    ``renew_lease`` does (spec §16.7.4 R-F5) and never returns ``None``."""
    renewed = await _renew(db, task_id, worker_id)
    return LeaseFlags(cancel_requested=renewed.cancel_requested, pause_requested=renewed.pause_requested)


@dataclass(frozen=True)
class LeaseGuard:
    """A step's way to keep its lease inside the transaction of a write of its own (spec §16.5a #8).

    ``hold(db)`` renews the lease in the caller's transaction and raises ``LeaseLost`` when this worker no
    longer holds a live one, so a write that follows cannot land after a takeover. It does not check the
    task's status. Five rules:

    1. Call ``hold`` first in the transaction: the renewal locks the task row until commit, which is what
       makes the writes after it safe against a takeover.
    2. Only for short transactions: the row stays locked, and the heartbeat's renewal waits behind it. The
       server ends the session of a transaction that idles longer than ``IDLE_IN_TRANSACTION_TIMEOUT`` (30 s,
       set by ``bound_transaction``), so a step that holds and then waits on non-database IO is cut off.
    3. The caller opens the transaction with ``bound_transaction`` (and its own wall-clock timeout); ``hold``
       sets no bound.
    4. ``hold`` does not advance the heartbeat's ``last_ok``: the heartbeat still renews on its own schedule.
    5. After the engine revokes the owner (cleanup overrun, or the heartbeat gives up), ``hold`` raises
       ``LeaseLost`` without SQL. The step must stop and write nothing more.
    """

    task_id: uuid.UUID
    owner: str = field(repr=False)

    async def hold(self, db: AsyncSession) -> None:
        await renew_lease(db, self.task_id, self.owner)


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
        logger.warning("lease_release_not_owner", task_id=str(task_id))
    return released is not None
