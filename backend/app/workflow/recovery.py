"""Recovery sweep (ARCHITECTURE §5.3, RUNBOOK): run at worker start and every 30 s by the scheduler.

A ``running``/``validating`` task whose lease expired has lost its worker. It moves through the state
machine to ``interrupted``, or to ``cancelled`` if a cancel was requested, and its lease is cleared. The
user (or an auto-resume) can then queue it again from its checkpoint (AC-17). Rows are claimed with
``FOR UPDATE SKIP LOCKED``, so concurrent sweeps never transition a task twice.

When the ``agent_runs`` table lands (WP-3.x), a running agent_run is failed here as well.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.events import EventBus, commit_and_publish
from app.core.logging import get_logger
from app.models.tasks import Task
from app.workflow.states import Actor, TaskStatus, transition

ACTOR = Actor("system", "recovery")
_ACTIVE = (TaskStatus.RUNNING.value, TaskStatus.VALIDATING.value)

logger = get_logger(__name__)


@dataclass(frozen=True)
class RecoveryResult:
    recovered: int
    failed: int


def _orphaned():  # type: ignore[no-untyped-def]
    return (Task.status.in_(_ACTIVE)) & (Task.lease_expires_at < func.clock_timestamp())


async def recover_expired(
    sessions: async_sessionmaker[AsyncSession], bus: EventBus, audit: AuditWriter
) -> RecoveryResult:
    async with sessions() as db:
        candidates = (await db.scalars(select(Task.id).where(_orphaned()))).all()
    recovered = failed = 0
    for task_id in candidates:
        try:
            if await _recover_one(sessions, bus, audit, task_id):
                recovered += 1
        except Exception as exc:
            # One bad task must not block the others; it stays orphaned and is retried by the next sweep.
            failed += 1
            logger.error("task_recovery_failed", task_id=str(task_id), error_type=type(exc).__name__)
    if recovered or failed:
        logger.info("task_recovery_sweep", recovered=recovered, failed=failed)
    return RecoveryResult(recovered, failed)


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
        await transition(
            db,
            task,
            TaskStatus.CANCELLED if cancel else TaskStatus.INTERRUPTED,
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
        return True
