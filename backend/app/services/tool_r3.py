"""The ``tool_r3`` handler (ADR-024; API §11.1: approve → resume, reject → failed; spec WP-3.1d Goal 5).

The handler only moves the task; the gateway consumes the approval when the resumed run calls the tool again.
Approved: a task parked in ``waiting_approval`` goes back to where it was parked from (``draft`` during
planning, ADR-021; otherwise ``queued``). Rejected or expired: ``failed``. A finished task needs nothing. A
task not parked (yet) fails the handler visibly, and the sweep retries it: the decision may simply have been
faster than the engine parking the task.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.models.approvals import Approval
from app.models.tasks import Task, TaskEvent
from app.services import approval_store
from app.workflow.states import FINISHED, TaskStatus, transition
from app.workflow.states import Actor as WorkflowActor

ACTION = approval_store.TOOL_R3


def _reason(approval: Approval, decision: str) -> str:
    return f"tool_r3 approval {approval.id} {decision}"


class ToolR3Handler:
    def __init__(self, *, sessions: async_sessionmaker[AsyncSession], audit: AuditWriter) -> None:
        self._sessions = sessions
        self._audit = audit

    async def _locked(self, session: AsyncSession, approval_id: uuid.UUID) -> tuple[Approval, Task] | None:
        """Task first, then approval: the order the ToolGateway takes, so the two never deadlock."""
        task_id = await session.scalar(select(Approval.task_id).where(Approval.id == approval_id))
        if task_id is None:
            return None  # purged with its project, or not a task's approval
        task = await session.scalar(
            select(Task).where(Task.id == task_id).with_for_update().execution_options(populate_existing=True)
        )
        approval = await session.scalar(
            select(Approval)
            .where(Approval.id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return None if task is None or approval is None else (approval, task)

    async def _last_change(self, session: AsyncSession, task: Task) -> dict[str, object]:
        payload = await session.scalar(
            select(TaskEvent.payload)
            .where(TaskEvent.task_id == task.id, TaskEvent.type == "task.status_changed")
            .order_by(TaskEvent.seq.desc())
            .limit(1)
        )
        return payload or {}

    async def _move(self, approval_id: uuid.UUID, *, decision: str, statuses: tuple[str, ...]) -> None:
        async with self._sessions() as session:
            locked = await self._locked(session, approval_id)
            if locked is None:
                return
            approval, task = locked
            if approval.status not in statuses:
                return  # superseded meanwhile: a newer request owns the task's wait
            reason = _reason(approval, decision)
            current = TaskStatus(task.status)
            last = await self._last_change(session, task)
            if current in FINISHED or last.get("reason") == reason:
                return  # nothing left to move, or moved by an earlier run of this handler
            if current is not TaskStatus.WAITING_APPROVAL:
                raise RuntimeError(f"task is {current.value}, not waiting for this approval")
            others = await session.scalar(
                select(Approval.id).where(
                    Approval.task_id == task.id,
                    Approval.id != approval.id,
                    Approval.status == "pending",
                )
            )
            if others is not None:
                raise RuntimeError("the task also waits for another pending approval")
            if decision == "approved":
                parked_from = (
                    last.get("from") if last.get("to") == TaskStatus.WAITING_APPROVAL.value else None
                )
                target = TaskStatus.DRAFT if parked_from == TaskStatus.DRAFT.value else TaskStatus.QUEUED
            else:
                target = TaskStatus.FAILED
            await transition(
                session,
                task,
                target,
                reason=reason,
                actor=WorkflowActor("system", f"approval:{approval.id}"),
                audit=self._audit,
            )
            await session.commit()

    async def approved(self, approval_id: uuid.UUID, *, now: datetime | None) -> None:
        await self._move(approval_id, decision="approved", statuses=("approved", "consumed"))

    async def rejected(self, approval_id: uuid.UUID, *, now: datetime | None) -> None:
        await self._move(approval_id, decision="rejected", statuses=("rejected",))

    async def expired(self, approval_id: uuid.UUID, *, now: datetime | None) -> None:
        await self._move(approval_id, decision="expired", statuses=("expired",))
