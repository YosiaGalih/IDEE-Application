"""Port the engine uses to hand a task to a worker (the Celery adapter lives in app.services.job_queue), and
the sweep that hands a task over again when its queued job was lost.

**Orphaned dispatch.** A ``draft``/``queued`` task is a candidate when all of these hold:
- ``checkpoint.dispatch.at`` exists: a creator dispatched it. ``dispatch`` alone is not enough, because the
  engine's first draft claim creates it with only ``planning_attempts``;
- its lease is dead (NULL or expired, on the DB clock, as ``recovery.lease_dead``);
- ``dispatch.at`` is older than the grace period.

**Sweep.** Oldest ``dispatch.at`` first, at most ``DISPATCH_BATCH`` per sweep. Each candidate is re-checked
under ``FOR UPDATE SKIP LOCKED`` in its own short transaction, which advances ``dispatch.at`` and
``dispatch.count`` and commits before the enqueue. A failed enqueue therefore waits one more grace period
instead of being retried by every sweep. Re-sending is safe: the claim rejects duplicates.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import TIMESTAMP, ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.tasks import Task
from app.workflow.checkpoint import dispatch_int, with_dispatch

DISPATCH_GRACE_S = 60.0
DISPATCH_BATCH = 100
_WAITING = ("draft", "queued")

logger = get_logger(__name__)


class Dispatcher(Protocol):
    async def enqueue_task(self, task_id: uuid.UUID) -> None:
        """Hand the task to a worker; raise if it was not handed over."""
        ...


@dataclass(frozen=True)
class DispatchResult:
    candidates: int
    dispatched: int
    failed: int
    skipped: int  # locked, or no longer a candidate by the time it was locked; a later sweep sees it again


class DispatchIncomplete(Exception):
    """Some orphaned tasks were not handed over; raised so the job is marked failed, not successful."""

    def __init__(self, result: DispatchResult) -> None:
        super().__init__(f"{result.failed} of {result.candidates} orphaned dispatches were not sent")
        self.result = result

    def __reduce__(self) -> tuple[type[DispatchIncomplete], tuple[DispatchResult]]:
        return (DispatchIncomplete, (self.result,))  # Celery re-creates task exceptions from their args


def _dispatched_at() -> ColumnElement[datetime]:
    return func.jsonb_extract_path_text(Task.checkpoint, "dispatch", "at").cast(TIMESTAMP(timezone=True))


def _orphaned(grace_s: float) -> ColumnElement[bool]:
    now = func.clock_timestamp()
    return and_(
        Task.status.in_(_WAITING),
        Task.checkpoint["dispatch"].has_key("at"),
        or_(Task.lease_expires_at.is_(None), Task.lease_expires_at < now),
        _dispatched_at() < now - timedelta(seconds=grace_s),
    )


async def _list_candidates(
    sessions: async_sessionmaker[AsyncSession], grace_s: float, batch: int
) -> list[uuid.UUID]:
    async with sessions() as db:
        rows = await db.scalars(
            select(Task.id).where(_orphaned(grace_s)).order_by(_dispatched_at()).limit(batch)
        )
        return list(rows.all())


async def _mark_dispatched(
    sessions: async_sessionmaker[AsyncSession], task_id: uuid.UUID, grace_s: float
) -> int | None:
    """New ``dispatch.count``, or ``None`` when the row is locked or no longer a candidate."""
    async with sessions() as db:
        locked = await db.scalar(
            select(Task.id).where(Task.id == task_id, _orphaned(grace_s)).with_for_update(skip_locked=True)
        )
        if locked is None:
            return None
        count = await db.scalar(
            update(Task)
            .where(Task.id == task_id)
            .values(
                checkpoint=with_dispatch(
                    at=func.clock_timestamp(), count=func.coalesce(dispatch_int("count"), 0) + 1
                )
            )
            .returning(dispatch_int("count"))
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        return count


async def redispatch_orphans(
    sessions: async_sessionmaker[AsyncSession],
    dispatcher: Dispatcher,
    *,
    grace_s: float = DISPATCH_GRACE_S,
    batch: int = DISPATCH_BATCH,
) -> DispatchResult:
    candidates = await _list_candidates(sessions, grace_s, batch)
    dispatched = failed = skipped = 0
    for task_id in candidates:
        try:
            count = await _mark_dispatched(sessions, task_id, grace_s)
            if count is None:
                skipped += 1
                continue
            await dispatcher.enqueue_task(task_id)  # after the commit: no transaction is open here
            dispatched += 1
            logger.info("task_redispatched", task_id=str(task_id), count=count)
        except Exception as exc:  # not BaseException: the job's timeout cancels this coroutine
            # One bad task must not block the others; the advanced dispatch.at retries it after the grace.
            failed += 1
            logger.error("task_redispatch_failed", task_id=str(task_id), error_type=type(exc).__name__)
    result = DispatchResult(len(candidates), dispatched, failed, skipped)
    if failed:
        logger.warning("task_redispatch_sweep", **asdict(result))
    return result
