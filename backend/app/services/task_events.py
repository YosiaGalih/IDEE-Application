"""Task event history, live fan-out and the event stream (API_CONTRACTS §5.1; spec WP-3.2 §13, #27-#35).

Framework-free. ``history`` returns the stored events of a task in ``seq`` order, in the message shape the
live stream publishes. ``TaskEventHub`` is the live side: one Redis pattern subscription per api process,
fanned out to bounded per-stream queues. ``stream_frames`` merges both into SSE frames without gaps or
duplicates; the route wraps it (``open_stream`` first, before the response starts).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import math
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from typing import Any, Final, NamedTuple

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.events import event_message, task_channel
from app.core.logging import get_logger
from app.models.tasks import Task, TaskEvent
from app.services.auth import resolve_session
from app.services.projects import Actor, ProjectEnv, authorize, not_found
from app.workflow.states import FINISHED

HISTORY_LIMIT_MAX: Final = 500
HUB_QUEUE_MAX: Final = 1000
HUB_PATTERN: Final = "kk:task:*"  # matches task_channel(task_id)
HUB_BACKOFF_FIRST: Final = 0.5
HUB_BACKOFF_MAX: Final = 10.0
HUB_POLL_SECONDS: Final = 1.0
# The stream (spec §13.2 #28, #31). Module attributes, read at call time, so tests can make them small.
REPLAY_PAGE: Final = 500
PING_INTERVAL_S: Final = 15.0
AUTH_RECHECK_S: Final = 60.0
MAX_STREAM_S: Final = 3300.0  # 55 minutes, under Caddy's read_timeout of one hour
MAX_STREAMS_PER_USER: Final = 8  # spec §13.2 #32
MAX_STREAMS_TOTAL: Final = 256
STREAM_END: Final = "stream.end"
PING_FRAME: Final = ": ping\n\n"
# The ``event:`` line names a type only when it matches (#33); a type that does not is never sent.
EVENT_TYPE: Final = re.compile(r"[a-z][a-z0-9_.]{0,59}")

logger = get_logger(__name__)


async def _authorize_read(session: AsyncSession, actor: Actor, task_id: uuid.UUID) -> uuid.UUID:
    """The task's project after ``task:read`` there; ``not_found()`` for a missing or out-of-scope task."""
    project_id = await session.scalar(select(Task.project_id).where(Task.id == task_id))
    if project_id is None:
        raise not_found()
    await authorize(session, actor, project_id, "task:read")
    return project_id


async def _event_page(
    session: AsyncSession, task_id: uuid.UUID, after_seq: int, limit: int
) -> list[dict[str, Any]]:
    """Stored events with ``seq > after_seq`` in ``seq`` order, as messages: history, replay and gap fill."""
    rows = (
        await session.scalars(
            select(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.seq > after_seq)
            .order_by(TaskEvent.seq)
            .limit(limit)
        )
    ).all()
    return [event_message(row.task_id, row.seq, row.type, row.created_at, row.payload) for row in rows]


async def history(
    env: ProjectEnv, actor: Actor, task_id: uuid.UUID, *, after_seq: int, limit: int
) -> tuple[list[dict[str, Any]], int | None]:
    """``(messages by ascending seq, next_after_seq)``; the cursor is the last seq when the page is full.

    ``not_found()`` when the task does not exist or its project is out of the actor's scope.
    """
    async with env.sessions() as session:
        await _authorize_read(session, actor, task_id)
        messages = await _event_page(session, task_id, after_seq, limit)
    return messages, (messages[-1]["seq"] if len(messages) == limit else None)


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


# --- the stream (spec §13.2 #28-#31, #33; §13.5 #2, #3, #7) -----------------------------------------------


@dataclasses.dataclass(frozen=True)
class StreamTimings:
    """A stream's intervals in seconds: ping and database check, access recheck, connection lifetime.

    Each is a positive finite number: zero or less would ping, recheck or close on every pass.
    """

    ping_s: float = PING_INTERVAL_S
    auth_recheck_s: float = AUTH_RECHECK_S
    max_stream_s: float = MAX_STREAM_S

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            value = getattr(self, field.name)
            if not (isinstance(value, int | float) and math.isfinite(value) and value > 0):
                raise ValueError(f"{field.name} must be a positive number of seconds")


class _TaskRow(NamedTuple):
    project_id: uuid.UUID
    status: str
    event_seq: int


def format_frame(message: dict[str, Any]) -> str | None:
    """One SSE frame for a stored or published event message; ``None`` when it may not be sent.

    ``id:`` only for a stored event (an int ``seq``). ``data`` is the whole message as one line of JSON:
    ``json.dumps`` escapes ``\\n`` and ``\\r`` inside strings, so a payload can neither end the frame nor add
    an ``id:``/``event:`` line; ASCII only (ruling R4), so a lone surrogate cannot break the UTF-8
    response. ``event:`` carries ``type`` only when it matches ``EVENT_TYPE``.
    """
    type_ = message.get("type")
    seq = message.get("seq")
    if not isinstance(type_, str) or not EVENT_TYPE.fullmatch(type_):
        return None
    if seq is not None and (not isinstance(seq, int) or isinstance(seq, bool)):
        return None
    data = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
    head = "" if seq is None else f"id: {seq}\n"
    return f"{head}event: {type_}\ndata: {data}\n\n"


def _end_frame(task_id: uuid.UUID, status: str | None) -> str:
    """``stream.end`` (no ``id``): the task is finished and fully sent, or gone (``status`` null)."""
    data = json.dumps({"task_id": str(task_id), "status": status}, ensure_ascii=True, separators=(",", ":"))
    return f"event: {STREAM_END}\ndata: {data}\n\n"


def _sendable(task_id: uuid.UUID, message: dict[str, Any]) -> str | None:
    frame = format_frame(message)
    if frame is None:
        # Logged, never sent; a stored one still moves the cursor, or every check would read it again.
        logger.warning(
            "task_stream_event_skipped",
            task_id=str(task_id),
            seq=message.get("seq"),
            type=message.get("type"),
        )
    return frame


async def open_stream(env: ProjectEnv, actor: Actor, task_id: uuid.UUID) -> None:
    """The check before a stream starts (the route runs it before the response): ``task:read``, else
    ``not_found()`` for a missing or out-of-scope task, as ``history``."""
    async with env.sessions() as session:
        await _authorize_read(session, actor, task_id)


@contextmanager
def _listen(hub: TaskEventHub | None, task_id: uuid.UUID) -> Iterator[TaskListener | None]:
    if hub is not None and hub.running:
        with hub.listen(task_id) as listener:
            yield listener
        return
    # No live channel (no lifespan, or Redis was down at start): the per-ping database check still delivers
    # every stored event, only later (spec §13.5 #2).
    logger.warning("task_stream_without_hub", task_id=str(task_id))
    yield None


async def _task_row(env: ProjectEnv, task_id: uuid.UUID) -> _TaskRow | None:
    async with env.sessions() as session:
        row = (
            await session.execute(
                select(Task.project_id, Task.status, Task.event_seq).where(Task.id == task_id)
            )
        ).one_or_none()
    return None if row is None else _TaskRow(*row)


async def _read_page(env: ProjectEnv, task_id: uuid.UUID, after_seq: int) -> list[dict[str, Any]]:
    async with env.sessions() as session:  # one short session per page; none is held while frames go out
        return await _event_page(session, task_id, after_seq, REPLAY_PAGE)


async def _may_still_read(
    env: ProjectEnv, settings: Settings, session_token: str, actor: Actor, task: _TaskRow, task_id: uuid.UUID
) -> bool:
    """Reload the actor from its session, as ``current_actor`` does, and check ``task:read`` again (#31).

    Read only (ruling R2): ``touch=False`` never slides ``last_seen_at`` nor revokes, so an open stream
    does not keep an idle session alive; an idle-expired session closes the stream.
    """
    try:
        user, _, grants = await resolve_session(env.sessions, settings, session_token, touch=False)
        if user.id != actor.user_id:  # one token is one user; never judge another user's grants
            raise not_found()
        reloaded = dataclasses.replace(actor, grants=tuple(grants))
        async with env.sessions() as session:
            await authorize(session, reloaded, task.project_id, "task:read")
    except AppError as exc:
        logger.info("task_stream_closed", task_id=str(task_id), reason="access", code=exc.code)
        return False
    return True


async def _next_message(listener: TaskListener | None, wait_s: float) -> dict[str, Any] | None:
    """The next live message, or ``None`` after ``wait_s`` seconds; without a live channel it only waits."""
    if wait_s <= 0:
        return None
    if listener is None:
        await asyncio.sleep(wait_s)
        return None
    try:
        async with asyncio.timeout(wait_s):
            return await listener.queue.get()
    except TimeoutError:
        return None


async def stream_frames(
    env: ProjectEnv,
    settings: Settings,
    hub: TaskEventHub | None,
    *,
    session_token: str,
    actor: Actor,
    task_id: uuid.UUID,
    after_seq: int,
    timings: StreamTimings = StreamTimings(),  # noqa: B008 - frozen value
    clock: Callable[[], float] = time.monotonic,
) -> AsyncGenerator[str, None]:
    """SSE frames of one task's events after ``after_seq``: each stored event exactly once, in ``seq`` order.

    The listener is registered before the replay, so nothing published meanwhile is missed. State:
    ``cursor`` is the last stored seq sent (or skipped); ``behind`` means the database may hold events after
    it; ``finished`` is ``(status, event_seq)`` from the last check of a finished task. Each pass does the
    first that applies:

    1. lifetime over -> return (no ``stream.end``: the client reconnects with ``Last-Event-ID``);
    2. check due (first pass, then after each ``: ping``): read ``status``/``event_seq``; task gone ->
       ``stream.end`` with ``status`` null; ``event_seq > cursor`` -> behind;
    3. access recheck due (read only) -> task gone -> ``stream.end`` null; session or ``task:read`` lost ->
       return (no ``stream.end``: the reconnect gets 401/404);
    4. behind -> one page of ``seq > cursor`` (own short session), sent in order; behind while pages are full;
    5. finished and ``cursor >= event_seq`` -> ``stream.end`` (after the terminal ``task.status_changed``);
    6. listener lagged -> behind;
    7. wait for a live message until the next deadline: ``seq`` null -> sent without ``id``;
       ``seq <= cursor`` -> dropped; ``cursor + 1`` -> sent; beyond -> behind (the database has it and the
       gap before it: commit precedes publish).

    No database session is open while a frame is out or while waiting.
    """
    started = clock()
    deadline = started + timings.max_stream_s
    next_ping = started + timings.ping_s
    next_auth = started + timings.auth_recheck_s
    cursor = after_seq
    behind = True  # the replay
    check = True  # once before the replay: a finished task ends after it without waiting for a ping
    finished: tuple[str, int] | None = None
    with _listen(hub, task_id) as listener:
        while True:
            now = clock()
            if now >= deadline:
                logger.info("task_stream_closed", task_id=str(task_id), reason="max_lifetime")
                return
            if check or now >= next_ping:
                if not check:
                    next_ping = now + timings.ping_s
                    yield PING_FRAME
                check = False
                row = await _task_row(env, task_id)
                if row is None:
                    yield _end_frame(task_id, None)
                    return
                behind = behind or row.event_seq > cursor
                finished = (row.status, row.event_seq) if row.status in FINISHED else None
                continue
            if now >= next_auth:  # before the replay pages: a long backlog never postpones it
                next_auth = now + timings.auth_recheck_s
                row = await _task_row(env, task_id)
                if row is None:  # spec §13.5 #7: a missing task ends the stream before access is judged
                    yield _end_frame(task_id, None)
                    return
                if not await _may_still_read(env, settings, session_token, actor, row, task_id):
                    return
                continue
            if behind:
                page = await _read_page(env, task_id, cursor)
                behind = len(page) == REPLAY_PAGE
                for message in page:
                    cursor = message["seq"]
                    if (frame := _sendable(task_id, message)) is not None:
                        yield frame
                continue
            if finished is not None and cursor >= finished[1]:
                yield _end_frame(task_id, finished[0])
                return
            if listener is not None and listener.take_lagged():
                behind = True
                continue
            message = await _next_message(listener, min(next_ping, next_auth, deadline) - now)
            if message is None:
                continue
            seq = message.get("seq")  # the hub passes a message without "seq" as ephemeral
            if seq is None:
                if (frame := _sendable(task_id, message)) is not None:
                    yield frame
            elif seq == cursor + 1:
                cursor = seq
                if (frame := _sendable(task_id, message)) is not None:
                    yield frame
            elif seq > cursor:
                behind = True


class StreamSlot:
    """One open stream's place. ``release`` is idempotent: the generator's ``finally`` and the response's
    background task both call it, and a second call must never free someone else's place."""

    def __init__(self, slots: StreamSlots, user_id: uuid.UUID) -> None:
        self._slots: StreamSlots | None = slots
        self._user_id = user_id

    def release(self) -> None:
        slots, self._slots = self._slots, None
        if slots is not None:
            slots._free(self._user_id)


class StreamSlots:
    """Open streams counted in memory: ``per_user`` for one user, ``total`` for the process (spec §13.2 #32).

    Right for one process (tier S); higher tiers move the counter to Redis. Synchronous and without awaits,
    so the check and the count are one step on the event loop.
    """

    def __init__(self, per_user: int = MAX_STREAMS_PER_USER, total: int = MAX_STREAMS_TOTAL) -> None:
        self._per_user = per_user
        self._total = total
        self._open: dict[uuid.UUID, int] = {}

    def acquire(self, user_id: uuid.UUID) -> StreamSlot:
        if self._open.get(user_id, 0) >= self._per_user or sum(self._open.values()) >= self._total:
            raise AppError(
                "RATE_LIMITED",
                "Too many open event streams",
                http_status=429,
                details=[{"reason": "too_many_streams"}],
            )
        self._open[user_id] = self._open.get(user_id, 0) + 1
        return StreamSlot(self, user_id)

    def _free(self, user_id: uuid.UUID) -> None:
        count = self._open.get(user_id, 0) - 1
        if count > 0:
            self._open[user_id] = count
        else:
            self._open.pop(user_id, None)
