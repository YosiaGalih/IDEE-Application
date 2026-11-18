"""Task event history and live fan-out (API_CONTRACTS §5.1; spec WP-3.2 §13, Lead decisions #27, #34).

Framework-free. ``history`` returns the stored events of a task in ``seq`` order, in the message shape the
live stream publishes. ``TaskEventHub`` is the live side: one Redis pattern subscription per api process,
fanned out to bounded per-stream queues.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from sqlalchemy import select

from app.core.events import event_message, task_channel
from app.core.logging import get_logger
from app.models.tasks import Task, TaskEvent
from app.services.projects import Actor, ProjectEnv, authorize, not_found

HISTORY_LIMIT_MAX: Final = 500
HUB_QUEUE_MAX: Final = 1000
HUB_PATTERN: Final = "kk:task:*"  # matches task_channel(task_id)
HUB_BACKOFF_FIRST: Final = 0.5
HUB_BACKOFF_MAX: Final = 10.0
HUB_POLL_SECONDS: Final = 1.0

logger = get_logger(__name__)


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


class TaskListener:
    """One stream's bounded queue. A message that does not fit is dropped and ``lagged`` is set: the stream
    then re-reads from the database instead of trusting the queue (spec §13.2)."""

    def __init__(self, queue_max: int) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_max)
        self.lagged = False

    def put(self, message: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.lagged = True

    def take_lagged(self) -> bool:
        lagged, self.lagged = self.lagged, False
        return lagged


class TaskEventHub:
    """One Redis pubsub connection per api process, ``kk:task:*``, routed to the listeners of each task.

    Own client without ``socket_timeout`` (an idle subscription is normal) and with ``health_check_interval``.
    Never raises into streams and its reader never dies: trouble (a lost connection, a full queue, a message
    that is not an event) shows as ``listener.lagged`` or is logged and dropped.
    """

    def __init__(self, redis_url: str, *, queue_max: int = HUB_QUEUE_MAX) -> None:
        self._redis_url = redis_url
        self._queue_max = queue_max
        self._listeners: dict[uuid.UUID, set[TaskListener]] = {}
        self._redis: Redis | None = None
        self._task: asyncio.Task[None] | None = None
        self._subscribed = asyncio.Event()
        # Unique per hub so a test (or an operator) can find and kill exactly this connection.
        self.client_name = f"kk-task-event-hub-{os.getpid()}-{uuid.uuid4().hex[:8]}"

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def subscribed(self) -> bool:
        """True only while the pattern subscription is live; False while connecting, retrying or stopped."""
        return self._subscribed.is_set()

    async def wait_subscribed(self, timeout: float) -> None:  # noqa: ASYNC109 - the caller's bound, as asked
        """Wait until the subscription is live; ``TimeoutError`` when it is not within ``timeout`` seconds."""
        await asyncio.wait_for(self._subscribed.wait(), timeout)

    async def start(self) -> None:
        if self.running:
            return
        await self._close_client()  # a crashed run leaves its client behind
        # No retry on the hub's own connection: redis-py would otherwise reconnect silently inside
        # ``get_message`` and the gap would never be reported as ``lagged``.
        self._redis = Redis.from_url(
            self._redis_url,
            health_check_interval=30,
            socket_keepalive=True,
            client_name=self.client_name,
            retry=Retry(NoBackoff(), 0),
        )
        self._task = asyncio.create_task(self._run(self._redis), name="task-event-hub")

    async def stop(self) -> None:
        task, self._task = self._task, None
        self._subscribed.clear()
        try:
            if task is not None:
                task.cancel()
                await asyncio.wait({task})  # a cancellation of our caller is not swallowed here
                if not task.cancelled() and (exc := task.exception()) is not None:
                    logger.error("task_event_hub_failed", error=type(exc).__name__)
        finally:
            await self._close_client()

    async def _close_client(self) -> None:
        redis, self._redis = self._redis, None
        if redis is not None:
            with contextlib.suppress(Exception):
                await redis.aclose()

    @contextmanager
    def listen(self, task_id: uuid.UUID) -> Iterator[TaskListener]:
        listener = TaskListener(self._queue_max)
        self._listeners.setdefault(task_id, set()).add(listener)
        try:
            yield listener
        finally:
            group = self._listeners.get(task_id)
            if group is not None:
                group.discard(listener)
                if not group:
                    del self._listeners[task_id]

    def _mark_all_lagged(self) -> None:
        for group in self._listeners.values():
            for listener in group:
                listener.lagged = True

    def _route(self, item: dict[str, Any]) -> None:
        """Deliver one pubsub item; anything that is not a well-formed event of its channel is dropped."""
        try:
            message = json.loads(item.get("data", b""))
            raw_id = message.get("task_id") if isinstance(message, dict) else None
            if not isinstance(raw_id, str):
                logger.warning("task_event_hub_bad_message", reason="no_task_id")  # never the payload
                return
            task_id = uuid.UUID(raw_id)
        except (ValueError, TypeError, AttributeError, RecursionError):
            logger.warning("task_event_hub_bad_message", reason="not_json")
            return
        if item.get("channel") != task_channel(task_id).encode():
            logger.warning("task_event_hub_bad_message", reason="channel_mismatch")
            return
        seq = message.get("seq")
        if (
            not (seq is None or (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 1))
            or not isinstance(message.get("type"), str)
            or not isinstance(message.get("ts"), str)
            or not isinstance(message.get("payload"), dict)
        ):
            logger.warning("task_event_hub_bad_message", reason="shape")
            return
        for listener in tuple(self._listeners.get(task_id, ())):
            listener.put(message)

    async def _run(self, redis: Redis) -> None:
        delay = HUB_BACKOFF_FIRST
        while True:
            pubsub: PubSub = redis.pubsub()
            try:
                await pubsub.psubscribe(HUB_PATTERN)
                # Events published while the subscription was down are gone: streams must re-read the history.
                self._mark_all_lagged()
                self._subscribed.set()
                delay = HUB_BACKOFF_FIRST
                while True:
                    item = await pubsub.get_message(ignore_subscribe_messages=True, timeout=HUB_POLL_SECONDS)
                    if item is not None:
                        self._route(item)
            except (TimeoutError, ConnectionError, RedisError, OSError) as exc:
                self._subscribed.clear()
                self._mark_all_lagged()
                logger.warning("task_event_hub_reconnecting", error=type(exc).__name__, backoff=delay)
            except Exception as exc:  # the reader must never die: report through lagged and start over
                self._subscribed.clear()
                self._mark_all_lagged()
                logger.error("task_event_hub_failed", error=type(exc).__name__, backoff=delay)
            finally:
                self._subscribed.clear()
                with contextlib.suppress(Exception):
                    await pubsub.aclose()
            await asyncio.sleep(delay)
            delay = min(delay * 2, HUB_BACKOFF_MAX)
