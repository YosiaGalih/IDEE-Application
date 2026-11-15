"""Property: whatever a transaction does with savepoints, the task events published equal the task events
stored (``app.core.events`` session hooks; WP-3.1f2b1 G3, AG-13 delta-2 LOW-2).

AG-13's fuzz, kept with fixed seeds: random programs of events, nested savepoints that are released or end
in an execute failure, a flush failure (raised or swallowed) or a Python error, savepoints opened before the
session's first event (no mark), and a final commit, rollback or root-level failure, followed by a second
clean transaction on the same session (a stale queue entry would surface there). 100 programs, one test.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db import make_sessionmaker
from app.core.events import emit_task_event, publish_pending
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from tests.conftest import reset_data

REDACTOR = Redactor()
PROGRAMS = 100
ENDINGS = ("release", "exec_fail", "flush_fail", "py_fail", "swallow_flush")
FINALS = ("commit", "commit", "rollback", "flush_fail_root", "exec_fail_root")

Step = tuple[Any, ...]


class Bus:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        self.messages.append(message)


class StepFailed(Exception):
    """A Python error inside a savepoint."""


def program(rng: random.Random, depth: int = 0) -> list[Step]:
    steps: list[Step] = []
    for _ in range(rng.randint(0, 4)):
        if depth < 3 and rng.random() < 0.45:
            steps.append(("savepoint", program(rng, depth + 1), rng.choice(ENDINGS)))
        else:
            steps.append(("say",))
    return steps


def broken_row() -> TaskEvent:
    """An event row of an unknown task: its FK fails at flush."""
    return TaskEvent(task_id=uuid.uuid4(), seq=1, type="x", payload={}, created_at=datetime.now(UTC))


async def run(
    session: AsyncSession, bus: Bus, task_id: uuid.UUID, steps: list[Step], said: list[int]
) -> None:
    for step in steps:
        if step[0] == "say":
            said[0] += 1
            await emit_task_event(
                session, bus, task_id, "task.log", {"message": f"m{said[0]}"}, redactor=REDACTOR
            )
            continue
        _, inner, ending = step
        try:
            async with session.begin_nested():
                await run(session, bus, task_id, inner, said)
                if ending == "exec_fail":
                    await session.execute(text("SELECT 1 / 0"))
                elif ending in ("flush_fail", "swallow_flush"):
                    session.add(broken_row())
                    try:
                        await session.flush()
                    except IntegrityError:
                        if ending == "flush_fail":
                            raise
                elif ending == "py_fail":
                    raise StepFailed
        except (DBAPIError, StepFailed):
            pass


async def test_published_events_equal_stored_events_for_random_savepoint_programs(
    engine: AsyncEngine,
) -> None:
    await reset_data(engine)
    sessions = make_sessionmaker(engine)
    async with sessions() as setup:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        setup.add(owner)
        await setup.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        setup.add(project)
        await setup.commit()
        owner_id, project_id = owner.id, project.id
    for seed in range(PROGRAMS):
        rng = random.Random(seed)  # noqa: S311 - a reproducible test program, not a secret
        async with sessions() as setup:
            task = Task(
                project_id=project_id, title="t", prompt="p", workflow_type="create", created_by=owner_id
            )
            setup.add(task)
            await setup.commit()
            task_id = task.id
        steps, final = program(rng), rng.choice(FINALS)
        bus, said = Bus(), [0]
        async with sessions() as session:
            await run(session, bus, task_id, steps, said)
            if final == "commit":
                await session.commit()
            elif final == "rollback":
                await session.rollback()
            else:
                try:
                    if final == "flush_fail_root":
                        session.add(broken_row())
                        await session.flush()
                    else:
                        await session.execute(text("SELECT 1 / 0"))
                except DBAPIError:
                    await session.rollback()
            said[0] += 1  # a second, clean transaction on the same session
            await emit_task_event(
                session, bus, task_id, "task.log", {"message": f"m{said[0]}"}, redactor=REDACTOR
            )
            await session.commit()
            await publish_pending(session, bus)
        async with sessions() as other:
            rows = await other.scalars(
                select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.seq)
            )
            stored = [(row.seq, row.payload["message"]) for row in rows]
        published = [(m["seq"], m["payload"]["message"]) for m in bus.messages]
        assert published == stored, (seed, steps, final)
