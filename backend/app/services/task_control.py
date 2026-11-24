"""Cancel a task from the API (WP-3.2f1, spec §16.7.2 #110, R-F1, R-F2, R-F7, R-F10). Framework-free.

Order: authorize (``task:control``), ``request_cancel`` under the row lock, commit, and only then kill the
task's sandbox runs, outside any DB session and inside ``KILL_TIMEOUT_S``. A kill that fails or hangs never
reaches the caller and never moves the status: it is marked instead (a ``task.log`` event and a
``task.cancel_unconfirmed`` audit, one short transaction) so the user and an auditor can see it.

Logs and the marker carry the error class only: never its message, which may hold manager output.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.events import emit_task_event
from app.core.logging import get_logger
from app.models.tasks import Task
from app.sandbox.client import SandboxClient, SandboxError
from app.services.projects import Actor, authorize, not_found
from app.services.tasks import TaskView, task_view
from app.workflow.control import request_cancel
from app.workflow.states import Actor as WorkflowActor

logger = get_logger(__name__)

KILL_TIMEOUT_S: Final = 10.0
CANCEL_UNCONFIRMED_MESSAGE: Final = "The sandbox could not confirm that the task's processes were stopped."


@dataclass(frozen=True)
class TaskControlEnv:
    sessions: async_sessionmaker[AsyncSession]
    audit: AuditWriter
    sandbox: SandboxClient


async def _kill(env: TaskControlEnv, actor: WorkflowActor, task_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Kill the task's runs with no session open; never raises (a failure is marked, R-F10)."""
    try:
        async with asyncio.timeout(KILL_TIMEOUT_S):
            killed = await env.sandbox.kill_task(task_id)
    except (SandboxError, TimeoutError) as exc:
        await _mark_unconfirmed(env, actor, task_id, project_id, type(exc).__name__)
        return
    if killed > 0:
        async with env.sessions() as session:
            await env.audit.write(
                session,
                actor_type=actor.type,
                actor_id=actor.id,
                action="sandbox.run_killed",
                result="success",
                resource_type="task",
                resource_id=str(task_id),
                project_id=project_id,
                metadata={"killed": killed},
            )
            await session.commit()


async def _mark_unconfirmed(
    env: TaskControlEnv, actor: WorkflowActor, task_id: uuid.UUID, project_id: uuid.UUID, error_type: str
) -> None:
    async with env.sessions() as session:
        await emit_task_event(
            session,
            None,
            task_id,
            "task.log",
            {"level": "warning", "code": "cancel_unconfirmed", "message": CANCEL_UNCONFIRMED_MESSAGE},
            redactor=env.audit.redactor,
        )
        await env.audit.write(
            session,
            actor_type=actor.type,
            actor_id=actor.id,
            action="task.cancel_unconfirmed",
            result="error",
            resource_type="task",
            resource_id=str(task_id),
            project_id=project_id,
            metadata={"error_type": error_type},
        )
        await session.commit()
    logger.warning("task_cancel_unconfirmed", task_id=str(task_id), error_type=error_type)


async def cancel_task(
    env: TaskControlEnv,
    actor: Actor,
    task_id: uuid.UUID,
    *,
    reason: str | None,
    iteration_max: int,
) -> TaskView:
    """Cancel ``task_id``: 404 missing or out of scope, 403 without ``task:control``, 409 when finished."""
    workflow_actor = WorkflowActor("user", str(actor.user_id))
    async with env.sessions() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise not_found()
        project_id = task.project_id
        await authorize(session, actor, project_id, "task:control")
        try:
            decision = await request_cancel(
                session, task_id, actor=workflow_actor, audit=env.audit, reason=reason
            )
        except LookupError:
            raise not_found() from None  # deleted between the load and the lock
        await session.commit()
    if decision.kill:
        await _kill(env, workflow_actor, task_id, project_id)
    async with env.sessions() as session:
        fresh = await session.get(Task, task_id, populate_existing=True)
        if fresh is None:
            raise not_found()
        return await task_view(session, fresh, iteration_max=iteration_max, actor=actor)
