"""Cancel request on a task (WP-3.2f1, spec §16.7.2 #101, #111, #112, R-F8): the API writer of ``cancelled``.

The writer of ``cancelled`` is a hybrid. This module sets the flag always and moves the task to
``cancelled`` itself only when no live lease holds it. With a live lease the engine reads the flag and
writes ``cancelled`` (spec §16.7.1); recovery does it for an expired lease. Everything runs under one
``SELECT ... FOR UPDATE`` in the caller's transaction: nothing here commits, and the caller kills the sandbox
processes after its commit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditWriter
from app.core.db import bound_transaction
from app.core.errors import AppError
from app.models.tasks import Task
from app.workflow.states import ALLOWED, TaskStatus, transition
from app.workflow.states import Actor as WorkflowActor

REASON = "cancel_requested"
REASON_MAX_LENGTH: Final = 200
_RUNNING = frozenset({TaskStatus.RUNNING.value, TaskStatus.VALIDATING.value})


@dataclass(frozen=True)
class CancelDecision:
    status_before: str
    cancelled_now: bool
    kill: bool
    noop: bool


async def request_cancel(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: WorkflowActor,
    audit: AuditWriter,
    reason: str | None,
) -> CancelDecision:
    """Ask for ``task_id`` to be cancelled under the row lock; the caller commits, then kills and publishes.

    Raises ``AppError("INVALID_TRANSITION")`` (409) for a ``completed`` or ``rejected`` task, and
    ``LookupError`` when the task does not exist.
    """
    await bound_transaction(session)
    locked = (
        await session.execute(select(Task.id).where(Task.id == task_id).with_for_update())
    ).one_or_none()
    if locked is None:
        raise LookupError("task not found")
    # Reloaded after the lock so the object, the flag and the lease are what the lock protects.
    task = (
        await session.execute(
            select(Task).where(Task.id == task_id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    live = bool(
        await session.scalar(
            select(
                (Task.lease_owner.is_not(None))
                & (Task.lease_expires_at.is_not(None))
                & (Task.lease_expires_at > func.clock_timestamp())
            ).where(Task.id == task_id)
        )
    )
    source = TaskStatus(task.status)
    kill = live or source.value in _RUNNING
    if source is TaskStatus.CANCELLED:
        return CancelDecision(source.value, cancelled_now=False, kill=False, noop=True)
    if TaskStatus.CANCELLED not in ALLOWED[source]:
        raise AppError(
            "INVALID_TRANSITION",
            f"A task cannot move from {source.value} to {TaskStatus.CANCELLED.value}",
            http_status=409,
            details=[{"from": source.value, "to": TaskStatus.CANCELLED.value}],
        )
    # Already flagged (a first call under a live lease): no second flag write and no second audit, but the
    # lease may have died since, so the decision below still runs (spec #101).
    already_flagged = task.cancel_requested  # read before the Core UPDATE; the object is stale after it
    if not already_flagged:
        # No status, no version bump: the engine's version-checked writes stay valid (R-F8).
        # synchronize_session=False leaves task.cancel_requested stale: callers use CancelDecision,
        # not the ORM attribute (or re-select).
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(cancel_requested=True)
            .execution_options(synchronize_session=False)
        )
        metadata: dict[str, Any] = {}
        if reason:
            metadata["reason"] = audit.redactor.redact(reason)[:REASON_MAX_LENGTH]
        await audit.write(
            session,
            actor_type=actor.type,
            actor_id=actor.id,
            action="task.cancel_requested",
            result="success",
            resource_type="task",
            resource_id=str(task_id),
            project_id=task.project_id,
            metadata=metadata or None,
        )
    if live:
        return CancelDecision(source.value, cancelled_now=False, kill=kill, noop=False)
    await transition(session, task, TaskStatus.CANCELLED, reason=REASON, actor=actor, audit=audit)
    return CancelDecision(source.value, cancelled_now=True, kill=kill, noop=False)
