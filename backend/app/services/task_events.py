"""Task event history (API_CONTRACTS §5.1; spec WP-3.2 §13, Lead decision #34). Framework-free.

The stored events of a task, in ``seq`` order, in the same message shape the live stream publishes.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from sqlalchemy import select

from app.core.events import event_message
from app.models.tasks import Task, TaskEvent
from app.services.projects import Actor, ProjectEnv, authorize, not_found

HISTORY_LIMIT_MAX: Final = 500


async def history(
    env: ProjectEnv, actor: Actor, task_id: uuid.UUID, *, after_seq: int, limit: int
) -> tuple[list[dict[str, Any]], int | None]:
    """``(messages by ascending seq, next_after_seq)``; the cursor is the last seq when the page is full.

    ``not_found()`` when the task does not exist or its project is out of the actor's scope.
    """
    async with env.sessions() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise not_found()
        await authorize(session, actor, task.project_id, "task:read")
        rows = (
            await session.scalars(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id, TaskEvent.seq > after_seq)
                .order_by(TaskEvent.seq)
                .limit(limit)
            )
        ).all()
    messages = [event_message(row.task_id, row.seq, row.type, row.created_at, row.payload) for row in rows]
    return messages, (rows[-1].seq if len(rows) == limit else None)
