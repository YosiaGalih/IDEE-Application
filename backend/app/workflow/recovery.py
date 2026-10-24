"""Recovery sweep (ARCHITECTURE §5.3, RUNBOOK): run at worker start and every 30 s by the scheduler.

**Orphaned task.** A ``running``/``validating`` task is orphaned when either:
- its lease expired; or
- it has no lease at all and has not changed for a grace period (a worker released the lease early or
  crashed between moving the task and claiming it).

**Recovery.**
- An orphaned task moves through the state machine to ``interrupted``, or to ``cancelled`` if a cancel was
  requested.
- Its lease is cleared.
- The user (or an auto-resume) can then queue it again from its checkpoint (AC-17).

**Concurrency.** Rows are claimed with ``FOR UPDATE SKIP LOCKED``, so concurrent sweeps never transition a
task twice. A skipped row is reported, not hidden.

When the ``agent_runs`` table lands (WP-3.x), a running agent_run is failed here as well.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.events import EventBus, commit_and_publish
from app.core.logging import get_logger
from app.models.tasks import Task
from app.workflow.lease import LEASE_SECONDS
from app.workflow.states import Actor, TaskStatus, transition

ACTOR = Actor("system", "recovery")
NO_LEASE_GRACE = timedelta(seconds=LEASE_SECONDS)
_ACTIVE = (TaskStatus.RUNNING.value, TaskStatus.VALIDATING.value)

logger = get_logger(__name__)


@dataclass(frozen=True)
class RecoveryResult:
    candidates: int
    recovered: int
    failed: int
    skipped: int  # locked by another sweep or a worker at that moment; the next sweep sees them again


class RecoveryIncomplete(Exception):
    """Some orphaned tasks could not be recovered; raised so the job is marked failed, not successful."""

    def __init__(self, result: RecoveryResult) -> None:
        super().__init__(f"{result.failed} of {result.candidates} orphaned tasks were not recovered")
        self.result = result

    def __reduce__(self) -> tuple[type[RecoveryIncomplete], tuple[RecoveryResult]]:
        return (RecoveryIncomplete, (self.result,))  # Celery re-creates task exceptions from their args


def _orphaned() -> ColumnElement[bool]:
    now = func.clock_timestamp()
    return and_(
        Task.status.in_(_ACTIVE),
        or_(
            Task.lease_expires_at < now,
            and_(Task.lease_expires_at.is_(None), Task.updated_at < now - NO_LEASE_GRACE),
        ),
    )


async def recover_expired(
    sessions: async_sessionmaker[AsyncSession], bus: EventBus, audit: AuditWriter
) -> RecoveryResult:
    async with sessions() as db:
        candidates = (await db.scalars(select(Task.id).where(_orphaned()))).all()
    recovered = failed = skipped = 0
    for task_id in candidates:
        try:
            if await _recover_one(sessions, bus, audit, task_id):
                recovered += 1
            else:
                skipped += 1
        except Exception as exc:
            # One bad task must not block the others; it stays orphaned and is retried by the next sweep.
            failed += 1
            logger.error(
                "task_recovery_failed",
                task_id=str(task_id),
                error_type=type(exc).__name__,
                error_code=getattr(exc, "code", None),
                exc_info=True,
            )
    result = RecoveryResult(len(candidates), recovered, failed, skipped)
    if failed:
        logger.warning("task_recovery_sweep", **result.__dict__)
    elif candidates:
        logger.info("task_recovery_sweep", **result.__dict__)
    return result


async def _recover_one(
    sessions: async_sessionmaker[AsyncSession], bus: EventBus, audit: AuditWriter, task_id: uuid.UUID
) -> bool:
    async with sessions() as db:
        task = await db.scalar(
            select(Task).where(Task.id == task_id, _orphaned()).with_for_update(skip_locked=True)
        )
        if task is None:  # another sweep has it, or the worker renewed in time
            return False
        cancel = task.cancel_requested
        lease_owner, lease_expired_at = task.lease_owner, task.lease_expires_at
        target = TaskStatus.CANCELLED if cancel else TaskStatus.INTERRUPTED
        await transition(
            db,
            task,
            target,
            reason="cancel_requested" if cancel else "lease_expired",
            actor=ACTOR,
            audit=audit,
        )
        await db.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(lease_owner=None, lease_expires_at=None)
            .execution_options(synchronize_session=False)
        )
        await commit_and_publish(db, bus)
        # Which worker vanished, and when: the trail an operator needs after a crash.
        logger.info(
            "task_recovered",
            task_id=str(task_id),
            to=target.value,
            lease_owner=lease_owner,
            lease_expired_at=lease_expired_at.isoformat() if lease_expired_at else None,
        )
        return True
