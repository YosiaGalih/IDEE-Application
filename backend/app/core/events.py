"""Task events (ARCHITECTURE §9, API_CONTRACTS §5.1).

A stored event takes its ``seq`` from ``tasks.event_seq`` inside the caller's transaction, so a task's
events are numbered without gaps or duplicates. Publishing to Redis happens only after the caller commits
(``publish_pending``): a subscriber must never see an event that was rolled back. Redis is a live channel,
not the record. A failed publish is logged, and clients recover through replay from the database.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redaction import Redactor
from app.models.tasks import Task, TaskEvent

MAX_TYPE_LENGTH = 60
_PENDING = "kk.pending_task_events"

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
    ephemeral: bool = False,
    redactor: Redactor | None = None,
) -> int | None:
    """Record (or, for ephemeral events, only publish) one task event; return its seq."""
    if not type or len(type) > MAX_TYPE_LENGTH:
        raise ValueError(f"event type must be 1-{MAX_TYPE_LENGTH} characters")
    safe_payload = (redactor or Redactor()).redact_obj(payload, stringify=True)
    now = datetime.now(UTC)
    if ephemeral:
        if bus is None:
            raise ValueError("an ephemeral event needs a bus: it is never stored")
        message = _message(task_id, None, type, now, safe_payload)
        await bus.publish(task_channel(task_id), message)
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
    db.info.setdefault(_PENDING, []).append(_message(task_id, seq, type, now, safe_payload))
    return seq


async def publish_pending(db: AsyncSession, bus: EventBus) -> None:
    """Publish events recorded in ``db`` once its transaction has committed."""
    pending: list[dict[str, Any]] = db.info.get(_PENDING) or []
    if not pending:
        return
    if db.in_transaction():
        raise RuntimeError("publish_pending must run after commit: the events are not durable yet")
    db.info[_PENDING] = []
    for message in pending:
        try:
            await bus.publish(task_channel(uuid.UUID(message["task_id"])), message)
        except Exception:
            # Live delivery only; the event is stored and clients replay it (ARCHITECTURE §9).
            logger.warning("task_event_publish_failed", task_id=message["task_id"], seq=message["seq"])


def discard_pending(db: AsyncSession) -> None:
    """Drop events queued in a transaction that rolled back."""
    db.info[_PENDING] = []


def _message(task_id: uuid.UUID, seq: int | None, type: str, ts: datetime, payload: Any) -> dict[str, Any]:
    return {"task_id": str(task_id), "seq": seq, "type": type, "ts": ts.isoformat(), "payload": payload}
