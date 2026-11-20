"""Per-key writes to ``tasks.checkpoint`` (spec WP-3.2 §14 #45, ruling R-T3e).

Every write goes through ``jsonb_set`` on one top-level key, never a whole-object overwrite, so writers of
other keys (``user_set``, ``dispatch.at``/``count``, ``completed_steps``) never erase each other. The helpers
run in the caller's transaction and never commit.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ColumnElement, Integer, Text, func, literal, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Task


def key_path(key: str) -> ColumnElement[Any]:
    """The ``text[]`` path ``jsonb_set`` takes for the top-level key ``key``."""
    return literal([key], ARRAY(Text))


def dispatch_int(name: str) -> ColumnElement[Any]:
    """``checkpoint.dispatch.<name>`` as an integer; NULL when ``dispatch`` or the field is missing."""
    return func.jsonb_extract_path_text(Task.checkpoint, "dispatch", name).cast(Integer)


def with_dispatch(**fields: Any) -> ColumnElement[Any]:
    """The checkpoint with ``fields`` merged into ``dispatch``; its other fields and the other keys stay.

    ``jsonb_set`` does not create a missing parent, so ``dispatch`` is built first: drafts made before 3.2d
    lack it.
    """
    pairs = [part for name, value in fields.items() for part in (literal(name, Text), value)]
    merged = func.coalesce(Task.checkpoint["dispatch"], literal({}, JSONB)).op("||")(
        func.jsonb_build_object(*pairs)
    )
    return func.jsonb_set(Task.checkpoint, key_path("dispatch"), merged)


async def count_planning_attempt(db: AsyncSession, task_id: uuid.UUID) -> int:
    """``dispatch.planning_attempts += 1``; returns the new count."""
    attempts = dispatch_int("planning_attempts")
    counted = await db.scalar(
        update(Task)
        .where(Task.id == task_id)
        .values(checkpoint=with_dispatch(planning_attempts=func.coalesce(attempts, 0) + 1))
        .returning(attempts)
        .execution_options(synchronize_session=False)
    )
    assert counted is not None
    return counted


async def add_completed_step(db: AsyncSession, task_id: uuid.UUID, step: str) -> None:
    """Append ``step`` to ``completed_steps`` unless it is already there."""
    completed = func.coalesce(Task.checkpoint["completed_steps"], literal([], JSONB))
    await db.execute(
        update(Task)
        .where(Task.id == task_id, ~Task.checkpoint.contains({"completed_steps": [step]}))
        .values(
            checkpoint=func.jsonb_set(
                Task.checkpoint, key_path("completed_steps"), completed.op("||")(literal([step], JSONB))
            )
        )
        .execution_options(synchronize_session=False)
    )
