"""Task events (ARCHITECTURE §9, API_CONTRACTS §5.1).

A stored event takes its ``seq`` from ``tasks.event_seq`` inside the caller's transaction, so a task's
events are numbered without gaps or duplicates.

Publishing to Redis happens only for events whose transaction committed:
- session hooks move queued events to a "committed" list on commit;
- they drop them on rollback, including a rolled-back savepoint;
- ``publish_pending`` (or ``commit_and_publish``) then sends only the committed ones. A subscriber can
  therefore never see an event the database does not have.

Redis is a live channel, not the record. A failed publish is logged with enough context to diagnose it, and
clients recover through replay from the database.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

from app.core.logging import get_logger
from app.core.redaction import Redactor
from app.models.tasks import Task, TaskEvent

MAX_TYPE_LENGTH = 60
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
    if not type or len(type) > MAX_TYPE_LENGTH:
        raise ValueError(f"event type must be 1-{MAX_TYPE_LENGTH} characters")
    safe_payload = redactor.redact_obj(payload, stringify=True)
    now = datetime.now(UTC)
    if ephemeral:
        if bus is None:
            raise ValueError("an ephemeral event needs a bus: it is never stored")
        await bus.publish(task_channel(task_id), _message(task_id, None, type, now, safe_payload))
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
    db.info.setdefault(_UNCOMMITTED, []).append(_message(task_id, seq, type, now, safe_payload))
    return seq


async def publish_pending(db: AsyncSession, bus: EventBus) -> None:
    """Publish the events of ``db`` whose transaction has committed. Safe to call at any time."""
    committed: list[dict[str, Any]] = db.info.get(_COMMITTED) or []
    if not committed:
        return
    db.info[_COMMITTED] = []
    sent = 0
    try:
        for message in committed:
            try:
                await bus.publish(task_channel(uuid.UUID(message["task_id"])), message)
            except Exception as exc:
                # Live delivery only; the event is stored and clients replay it (ARCHITECTURE §9).
                logger.warning(
                    "task_event_publish_failed",
                    task_id=message["task_id"],
                    seq=message["seq"],
                    type=message["type"],
                    error_type=type(exc).__name__,
                )
            sent += 1
    finally:
        if sent < len(committed):  # cancelled mid-way (shutdown, client gone)
            logger.warning("task_event_publish_abandoned", remaining=len(committed) - sent)


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
    session.info.setdefault(_COMMITTED, []).extend(session.info.get(_UNCOMMITTED, []))
    session.info[_UNCOMMITTED] = []
    session.info[_SAVEPOINTS] = {}


def _on_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    uncommitted = session.info.get(_UNCOMMITTED, [])
    mark = session.info.get(_SAVEPOINTS, {}).pop(id(previous_transaction), None)
    if previous_transaction.nested and mark is not None:
        del uncommitted[mark:]  # only the savepoint's events are gone
    else:
        uncommitted.clear()
        session.info[_SAVEPOINTS] = {}


def _message(task_id: uuid.UUID, seq: int | None, type: str, ts: datetime, payload: Any) -> dict[str, Any]:
    return {"task_id": str(task_id), "seq": seq, "type": type, "ts": ts.isoformat(), "payload": payload}
