"""Task events (ARCHITECTURE §9, API_CONTRACTS §5.1).

A stored event takes its ``seq`` from ``tasks.event_seq`` inside the caller's transaction, so a task's
events are numbered without gaps or duplicates.

Publishing to Redis happens only for events whose transaction committed:
- session hooks move queued events to a "committed" list on commit;
- they drop them on rollback: a full rollback drops every queued event, a rolled-back savepoint only the
  events queued inside it, also when a flush failure inside it rolls it back (WP-3.1f2b1 G3);
- ``publish_pending`` (or ``commit_and_publish``) then sends only the committed ones. A subscriber can
  therefore never see an event the database does not have.

Redis is a live channel, not the record. A failed publish is logged with enough context to diagnose it, and
clients recover through replay from the database.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Final, Protocol

from redis.asyncio import Redis
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.core.audit import json_safe
from app.core.logging import get_logger
from app.core.redaction import Redactor
from app.models.tasks import Task, TaskEvent

# A type is what the stream writes on its ``event:`` line (API_CONTRACTS §5.1), so it must fit there: a
# lower-case ASCII letter first, then letters, digits, ``_`` or ``.``, 60 characters at most (the column).
EVENT_TYPE: Final = re.compile(r"[a-z][a-z0-9_.]{0,59}")
# The stream's own frames (WP-3.2b ruling R5): no stored or published event may take these types.
STREAM_END: Final = "stream.end"
EVENT_INVALID: Final = "event.invalid"
RESERVED_TYPES: Final = frozenset({STREAM_END, EVENT_INVALID})
PUBLISH_TIMEOUT_SECONDS = 2.0
_UNCOMMITTED = "kk.task_events.uncommitted"
_COMMITTED = "kk.task_events.committed"
_SAVEPOINTS = "kk.task_events.savepoints"
_HOOKED = "kk.task_events.hooked"

logger = get_logger(__name__)


class EventBus(Protocol):
    async def publish(self, channel: str, message: dict[str, Any]) -> None: ...


class RedisEventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._redis.publish(channel, json.dumps(message, ensure_ascii=False))


def task_channel(task_id: uuid.UUID) -> str:
    return f"kk:task:{task_id}"


def is_event_type(value: object) -> bool:
    """Whether ``value`` may be an event's type: it matches ``EVENT_TYPE`` and is not the stream's own."""
    return isinstance(value, str) and EVENT_TYPE.fullmatch(value) is not None and value not in RESERVED_TYPES


async def emit_task_event(
    db: AsyncSession,
    bus: EventBus | None,
    task_id: uuid.UUID,
    type: str,
    payload: dict[str, Any],
    *,
    redactor: Redactor,
    ephemeral: bool = False,
) -> int | None:
    """Record (or, for ephemeral events, only publish) one task event; return its seq.

    ``redactor`` is required so callers pass the one that knows the configured secret literals.
    """
    if not is_event_type(type):
        raise ValueError(f"event type {repr(type)[:80]} must match {EVENT_TYPE.pattern} and not be reserved")
    # Redacted, then made JSON-safe (NaN, sets, non-str keys) before it reaches the database or Redis.
    safe_payload = json_safe(redactor.redact_obj(payload, stringify=True))
    now = datetime.now(UTC)
    if ephemeral:
        if bus is None:
            raise ValueError("an ephemeral event needs a bus: it is never stored")
        # Ephemeral events are lossy by design (deltas, output chunks): a Redis blip must not abort a run.
        await _publish(bus, event_message(task_id, None, type, now, safe_payload))
        return None

    seq = (
        await db.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(event_seq=Task.event_seq + 1)
            .returning(Task.event_seq)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if seq is None:
        raise ValueError(f"unknown task {task_id}")
    db.add(TaskEvent(task_id=task_id, seq=seq, type=type, payload=safe_payload, created_at=now))
    await db.flush()
    _hook(db.sync_session)
    db.info.setdefault(_UNCOMMITTED, []).append(event_message(task_id, seq, type, now, safe_payload))
    return seq


async def task_project_id(db: AsyncSession, task_id: uuid.UUID) -> uuid.UUID | None:
    """The project of the task, read only; ``None`` for an unknown task. For a module that emits a task's
    events but may not import the task model itself (the runtime's run store, under the "never moves a task"
    import contract), here the project of a ``budget.exceeded`` audit (WP-3.1f2b1 F6)."""
    return await db.scalar(select(Task.project_id).where(Task.id == task_id))


async def publish_pending(db: AsyncSession, bus: EventBus) -> None:
    """Publish the events of ``db`` whose transaction has committed. Safe to call at any time."""
    committed: list[dict[str, Any]] = db.info.get(_COMMITTED) or []
    if not committed:
        return
    db.info[_COMMITTED] = []
    sent = 0
    try:
        for message in committed:
            await _publish(bus, message)
            sent += 1
    finally:
        if sent < len(committed):  # cancelled mid-way (shutdown, client gone)
            logger.warning("task_event_publish_abandoned", remaining=len(committed) - sent)


async def _publish(bus: EventBus, message: dict[str, Any]) -> None:
    """Live delivery only: failures and hangs are logged; the stored event is replayed (ARCHITECTURE §9)."""
    try:
        async with asyncio.timeout(PUBLISH_TIMEOUT_SECONDS):
            await bus.publish(task_channel(uuid.UUID(message["task_id"])), message)
    except Exception as exc:
        logger.warning(
            "task_event_publish_failed",
            task_id=message["task_id"],
            seq=message["seq"],
            type=message["type"],
            error_type=type(exc).__name__,
        )


async def commit_and_publish(db: AsyncSession, bus: EventBus) -> None:
    """The commit path for work that emits task events."""
    await db.commit()
    await publish_pending(db, bus)


def _hook(session: Session) -> None:
    if session.info.get(_HOOKED):
        return
    session.info[_HOOKED] = True
    event.listen(session, "after_transaction_create", _on_transaction_create)
    event.listen(session, "after_commit", _on_commit)
    event.listen(session, "after_soft_rollback", _on_rollback)


def _on_transaction_create(session: Session, transaction: SessionTransaction) -> None:
    if transaction.nested:
        session.info.setdefault(_SAVEPOINTS, {})[id(transaction)] = len(session.info.get(_UNCOMMITTED, []))


def _on_commit(session: Session) -> None:
    # SQLAlchemy also dispatches after_commit when a savepoint is released; only the outermost commit counts.
    if session.in_nested_transaction():
        return
    session.info.setdefault(_COMMITTED, []).extend(session.info.get(_UNCOMMITTED, []))
    session.info[_UNCOMMITTED] = []
    session.info[_SAVEPOINTS] = {}


def _on_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    """Drop what the database rolled back. That is the boundary ``SessionTransaction.rollback`` rolls back:
    the nearest savepoint at or above the rolled-back transaction, else the whole transaction. A flush failure
    rolls back the flush's subtransaction (not nested, with a parent): inside a savepoint its boundary is
    that savepoint, so only the events queued since the savepoint began are gone (re-review N1). A savepoint
    without a mark was opened before the session's first event, so every queued event is its own
    (tests/integration/test_task_events.py, test_task_event_rollbacks.py)."""
    uncommitted = session.info.get(_UNCOMMITTED, [])
    savepoints = session.info.get(_SAVEPOINTS, {})
    boundary = previous_transaction
    while not boundary.nested and boundary.parent is not None:
        boundary = boundary.parent
    if boundary.nested:
        # Only the savepoint's own soft rollback forgets its mark (an execute-level failure, a Python error).
        # On the common flush path, ``async with begin_nested()`` finds the savepoint already DEACTIVE and
        # closes it instead (sqlalchemy/engine/util.py, TransactionalContext.__exit__): only the
        # subtransaction's rollback fires here, and the mark stays until the outer commit or a root rollback
        # resets the dict. Harmless: a closed savepoint's mark is never read again, and a new savepoint
        # writes its own mark on creation.
        own = boundary is previous_transaction
        mark = savepoints.pop(id(boundary), None) if own else savepoints.get(id(boundary))
        if mark is not None:
            del uncommitted[mark:]  # only the savepoint's events are gone
            return
    # The whole transaction, or a savepoint opened before the first event was queued (all events are its).
    uncommitted.clear()
    session.info[_SAVEPOINTS] = {}


def event_message(
    task_id: uuid.UUID, seq: int | None, type: str, ts: datetime, payload: Any
) -> dict[str, Any]:
    return {"task_id": str(task_id), "seq": seq, "type": type, "ts": ts.isoformat(), "payload": payload}
