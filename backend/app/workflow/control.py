"""Cancel request on a task (WP-3.2f1, spec §16.7.2 #101, #111, #112, R-F8): the API writer of ``cancelled``.

The writer of ``cancelled`` is a hybrid. This module sets the flag always and moves the task to
``cancelled`` itself only when no live lease holds it. With a live lease the engine reads the flag and
writes ``cancelled`` (spec §16.7.1); recovery does it for an expired lease. Everything runs under one
``SELECT ... FOR UPDATE`` in the caller's transaction: nothing here commits, and the caller kills the sandbox
processes after its commit.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditWriter
from app.core.db import bound_transaction
from app.core.errors import AppError
from app.models.tasks import Task
from app.workflow.states import ALLOWED, CANCEL_REASON, TaskStatus, transition
from app.workflow.states import Actor as WorkflowActor

REASON: Final = CANCEL_REASON  # the engine's and recovery's too (#105): one definition, in states
REASON_MAX_LENGTH: Final = 200
_LOCK_NOT_AVAILABLE: Final = "55P03"  # lock_not_available: the row stayed locked past lock_timeout
_RUNNING = frozenset({TaskStatus.RUNNING.value, TaskStatus.VALIDATING.value})


@dataclass(frozen=True)
class CancelDecision:
    status_before: str
    cancelled_now: bool
    kill: bool
    noop: bool


def _busy() -> AppError:
    """The task row stayed locked past ``lock_timeout`` (R11): 409, and ``Retry-After`` frees the key."""
    return AppError(
        "CONFLICT",
        "The task is busy; retry",
        http_status=409,
        details=[{"reason": "busy"}],
        headers={"Retry-After": "1"},
    )


async def flag_cancel_requested(session: AsyncSession, task_ids: Collection[uuid.UUID]) -> None:
    """Set ``cancel_requested`` on ``task_ids`` in one Core UPDATE, in the caller's transaction (R-F8).

    No status and no version bump: the engine's version-checked writes stay valid, and a live engine sees
    the flag at its next boundary. ``synchronize_session=False`` leaves a loaded ``Task.cancel_requested``
    stale: callers decide from what they read before this call, or re-select.
    """
    await session.execute(
        update(Task)
        .where(Task.id.in_(list(task_ids)))
        .values(cancel_requested=True)
        .execution_options(synchronize_session=False)
    )


async def request_cancel(
    session: AsyncSession,
    task_id: uuid.UUID,
    *,
    actor: WorkflowActor,
    audit: AuditWriter,
    reason: str | None,
    ip: str | None = None,
) -> CancelDecision:
    """Ask for ``task_id`` to be cancelled under the row lock; the caller commits, then kills and publishes.

    Raises ``AppError("INVALID_TRANSITION")`` (409) for a ``completed`` or ``rejected`` task, ``_busy()``
    (409) when the row stays locked past ``lock_timeout``, and ``LookupError`` when the task does not exist.
    ``ip`` is the caller's, recorded with the audit.
    """
    await bound_transaction(session)
    try:
        locked = (
            await session.execute(select(Task.id).where(Task.id == task_id).with_for_update())
        ).one_or_none()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE:
            raise _busy() from None
        raise
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
        # task.cancel_requested is stale after this: callers use CancelDecision, not the ORM attribute.
        await flag_cancel_requested(session, [task_id])
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
            correlation_id=f"task:{task_id}",
            ip=ip,
        )
    if live:
        return CancelDecision(source.value, cancelled_now=False, kill=kill, noop=False)
    await transition(session, task, TaskStatus.CANCELLED, reason=REASON, actor=actor, audit=audit)
    return CancelDecision(source.value, cancelled_now=True, kill=kill, noop=False)
