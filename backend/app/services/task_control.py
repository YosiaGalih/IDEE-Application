"""Cancel a task from the API (WP-3.2f1, spec §16.7.2 #110, R-F1, R-F2, R-F7, R-F10). Framework-free.

Order: authorize (``task:control``), ``request_cancel`` under the row lock, commit, and only then kill the
task's sandbox runs, outside any DB session and inside ``KILL_TIMEOUT_S``. A kill that fails or hangs never
reaches the caller and never moves the status: it is marked instead (a ``task.log`` event and a
``task.cancel_unconfirmed`` audit, one short transaction) so the user and an auditor can see it. The caller
learns what the kill did (``CancelResult.kill``, R9) even when that marker could not be written.

Each record written after the commit is bounded by ``RECORD_TIMEOUT_S``. A record that fails or times out is
logged at error level and dropped: the committed cancel stands.

Logs and the marker carry the error class only: never its message, which may hold manager output.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.db import bound_transaction
from app.core.events import emit_task_event
from app.core.logging import get_logger
from app.models.tasks import Task
from app.sandbox.client import SandboxClient
from app.services.projects import Actor, authorize, not_found
from app.services.tasks import TaskView, task_view
from app.workflow.control import request_cancel
from app.workflow.states import Actor as WorkflowActor

logger = get_logger(__name__)

KILL_TIMEOUT_S: Final = 10.0
RECORD_TIMEOUT_S: Final = 5.0  # each post-commit record write: the run_killed audit, the unconfirmed marker
CANCEL_UNCONFIRMED_MESSAGE: Final = "The sandbox could not confirm that the task's processes were stopped."

# What the kill of the task's sandbox runs did (R9): none was needed, it returned, or it raised or timed out
# (whether or not the unconfirmed marker was then written).
KillState = Literal["not_needed", "confirmed", "unconfirmed"]


@dataclass(frozen=True)
class TaskControlEnv:
    sessions: async_sessionmaker[AsyncSession]
    audit: AuditWriter
    sandbox: SandboxClient


@dataclass(frozen=True)
class CancelResult:
    view: TaskView
    kill: KillState


@dataclass(frozen=True)
class _Subject:
    """Who cancelled which task: what every post-commit record names (ids and the caller's ip only)."""

    actor: WorkflowActor
    ip: str | None
    task_id: uuid.UUID
    project_id: uuid.UUID

    @property
    def correlation_id(self) -> str:
        return f"task:{self.task_id}"


async def _kill(env: TaskControlEnv, subject: _Subject) -> KillState:
    """Kill the task's runs with no session open; never raises (a failure is marked, R-F10).

    The cancel is already committed here, so any ``Exception`` from the kill is an unconfirmed kill and any
    from the records is logged and dropped. ``CancelledError`` is not an ``Exception`` and still propagates.
    """
    try:
        async with asyncio.timeout(KILL_TIMEOUT_S):
            killed = await env.sandbox.kill_task(subject.task_id)
    except Exception as exc:
        kill_error_type = type(exc).__name__
        await _record(
            "cancel_unconfirmed",
            subject,
            partial(_mark_unconfirmed, env, subject, kill_error_type),
            kill_error_type=kill_error_type,
        )
        return "unconfirmed"
    if killed > 0:
        await _record("run_killed", subject, partial(_audit_killed, env, subject, killed))
    return "confirmed"


async def _record(
    record: str,
    subject: _Subject,
    write: Callable[[], Awaitable[None]],
    *,
    kill_error_type: str | None = None,
) -> None:
    """One post-commit record within ``RECORD_TIMEOUT_S``. A failure, a timeout included (``TimeoutError``),
    is logged at error level by type and dropped; a lost marker also names the kill's error type."""
    try:
        async with asyncio.timeout(RECORD_TIMEOUT_S):
            await write()
    except Exception as exc:
        kill = {} if kill_error_type is None else {"kill_error_type": kill_error_type}
        logger.error(
            "task_cancel_record_failed",
            task_id=str(subject.task_id),
            record=record,
            error_type=type(exc).__name__,
            **kill,
        )


async def _audit_killed(env: TaskControlEnv, subject: _Subject, killed: int) -> None:
    async with env.sessions() as session:
        await bound_transaction(session)
        await env.audit.write(
            session,
            actor_type=subject.actor.type,
            actor_id=subject.actor.id,
            action="sandbox.run_killed",
            result="success",
            resource_type="task",
            resource_id=str(subject.task_id),
            project_id=subject.project_id,
            metadata={"killed": killed},
            correlation_id=subject.correlation_id,
            ip=subject.ip,
        )
        await session.commit()


async def _mark_unconfirmed(env: TaskControlEnv, subject: _Subject, error_type: str) -> None:
    """The ``task.log`` event and the ``task.cancel_unconfirmed`` audit, in one transaction (R-F10)."""
    async with env.sessions() as session:
        await bound_transaction(session)
        await emit_task_event(
            session,
            None,
            subject.task_id,
            "task.log",
            {"level": "warning", "code": "cancel_unconfirmed", "message": CANCEL_UNCONFIRMED_MESSAGE},
            redactor=env.audit.redactor,
        )
        await env.audit.write(
            session,
            actor_type=subject.actor.type,
            actor_id=subject.actor.id,
            action="task.cancel_unconfirmed",
            result="error",
            resource_type="task",
            resource_id=str(subject.task_id),
            project_id=subject.project_id,
            metadata={"error_type": error_type},
            correlation_id=subject.correlation_id,
            ip=subject.ip,
        )
        await session.commit()
    logger.warning("task_cancel_unconfirmed", task_id=str(subject.task_id), error_type=error_type)


async def cancel_task(
    env: TaskControlEnv,
    actor: Actor,
    task_id: uuid.UUID,
    *,
    reason: str | None,
    iteration_max: int,
) -> CancelResult:
    """Cancel ``task_id``: 404 missing or out of scope, 403 without ``task:control``, 409 when finished or
    when the row stays locked (busy). The result carries what the kill did (R9)."""
    workflow_actor = WorkflowActor("user", str(actor.user_id))
    async with env.sessions() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise not_found()
        project_id = task.project_id
        await authorize(session, actor, project_id, "task:control")
        try:
            decision = await request_cancel(
                session, task_id, actor=workflow_actor, audit=env.audit, reason=reason, ip=actor.ip
            )
        except LookupError:
            raise not_found() from None  # deleted between the load and the lock
        await session.commit()
    kill: KillState = "not_needed"
    if decision.kill:
        kill = await _kill(env, _Subject(workflow_actor, actor.ip, task_id, project_id))
    async with env.sessions() as session:
        fresh = await session.get(Task, task_id, populate_existing=True)
        if fresh is None:
            raise not_found()
        view = await task_view(session, fresh, iteration_max=iteration_max, actor=actor)
    return CancelResult(view, kill)
