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
import random
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
from app.core.events import EVENT_INVALID, STREAM_END, event_message, is_event_type, task_channel
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
# The stream (spec §13.2 #28, #31). The intervals are ``StreamTimings`` defaults, bound when that class is
# defined: a test passes its own ``StreamTimings``. ``REPLAY_PAGE`` is read at call time (tests patch it).
REPLAY_PAGE: Final = 500
PING_INTERVAL_S: Final = 15.0
AUTH_RECHECK_S: Final = 60.0
MAX_STREAM_S: Final = 3300.0  # 55 minutes; each stream's own lifetime is jittered (``jittered_timings``)
LIFETIME_JITTER: Final = 0.1  # +-10 %: streams opened together (after a deploy) do not reconnect together
STREAM_CLOSE_GRACE_S: Final = 30.0  # past its lifetime a response is cut: a client that stopped reading
STREAMS_RETRY_AFTER_S: Final = 5  # the 429 for too many streams
MAX_STREAMS_PER_USER: Final = 8  # spec §13.2 #32
MAX_STREAMS_TOTAL: Final = 256
PING_FRAME: Final = ": ping\n\n"
STATUS_CHANGED: Final = "task.status_changed"  # workflow.states.transition's event: the status may be final
LOG_VALUE_MAX: Final = 80  # an untrusted string in a log line: its repr, cut to this length

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
    then re-reads from the database instead of trusting the queue (spec §13.2). The first drop of an episode
    (until the stream takes the flag) is logged, without the message."""

    def __init__(self, task_id: uuid.UUID, queue_max: int) -> None:
        self.task_id = task_id
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=queue_max)
        self.lagged = False
        self._dropping = False

    def put(self, message: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.lagged = True
            if not self._dropping:
                self._dropping = True
                logger.warning("task_stream_lagged", task_id=str(self.task_id), reason="queue_full")

    def take_lagged(self) -> bool:
        lagged, self.lagged, self._dropping = self.lagged, False, False
        return lagged


class TaskEventHub:
    """One Redis pubsub connection per api process, ``kk:task:*``, routed to the listeners of each task.

    Own client without ``socket_timeout`` (an idle subscription is normal) and with ``health_check_interval``.
    Never raises into streams and its reader never dies: trouble (a lost connection, a full queue, a message
    that is not an event) shows as ``listener.lagged`` or is logged and dropped. Should the reader stop all
    the same, other than by ``stop()``, that is logged (``task_event_hub_stopped``) and ``running`` turns
    false.
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
        task = asyncio.create_task(self._run(self._redis), name="task-event-hub")
        task.add_done_callback(self._on_reader_done)
        self._task = task

    def _on_reader_done(self, task: asyncio.Task[None]) -> None:
        if task is not self._task:  # stop() let it go, or a later start() replaced it
            return
        self._task = None
        self._subscribed.clear()
        error = "CancelledError" if task.cancelled() else type(task.exception()).__name__
        logger.error("task_event_hub_stopped", error=error)  # the type only: never a message or URL

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
        listener = TaskListener(task_id, self._queue_max)
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
        seq, type_, ts, payload = (message.get(key) for key in ("seq", "type", "ts", "payload"))
        if (
            not (seq is None or (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 1))
            or not isinstance(type_, str)
            or not isinstance(ts, str)
            or not isinstance(payload, dict)
        ):
            logger.warning("task_event_hub_bad_message", reason="shape")
            return
        # Rebuilt from the five checked fields: nothing else a publisher adds reaches a stream.
        event = {"task_id": str(task_id), "seq": seq, "type": type_, "ts": ts, "payload": payload}
        for listener in tuple(self._listeners.get(task_id, ())):
            listener.put(event)

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


def jittered_timings(rand: Callable[[], float] = random.random) -> StreamTimings:
    """The route's timings: the default intervals, the lifetime moved by up to ``LIFETIME_JITTER`` either way
    (``rand`` in [0, 1]), so streams that started together do not all reconnect together."""
    factor = 1 + LIFETIME_JITTER * (2 * rand() - 1)
    return StreamTimings(max_stream_s=MAX_STREAM_S * factor)


class _TaskRow(NamedTuple):
    project_id: uuid.UUID
    status: str
    event_seq: int


def format_frame(message: dict[str, Any]) -> str | None:
    """One SSE frame for a stored or published event message; ``None`` when it may not be sent as it is.

    ``id:`` only for a stored event (an int ``seq``). ``event:`` carries ``type`` only when it is an event
    type (``is_event_type``): never ``stream.end`` or ``event.invalid``, which only the stream writes (ruling
    R5). ``data`` is the whole message as one line of strict JSON: ``json.dumps`` escapes ``\\n`` and
    ``\\r`` inside strings, so a payload can neither end the frame nor add an ``id:``/``event:`` line; ASCII
    only (ruling R4), so a lone surrogate cannot break the UTF-8 response; no ``NaN``/``Infinity``, which a
    client's ``JSON.parse`` rejects.
    """
    type_ = message.get("type")
    seq = message.get("seq")
    if not is_event_type(type_):
        return None
    if seq is not None and (not isinstance(seq, int) or isinstance(seq, bool)):
        return None
    try:
        data = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError):
        return None
    head = "" if seq is None else f"id: {seq}\n"
    return f"{head}event: {type_}\ndata: {data}\n\n"


def _end_frame(task_id: uuid.UUID, status: str | None) -> str:
    """``stream.end`` (no ``id``): the task is finished and fully sent, or gone (``status`` null)."""
    data = json.dumps({"task_id": str(task_id), "status": status}, ensure_ascii=True, separators=(",", ":"))
    return f"event: {STREAM_END}\ndata: {data}\n\n"


def _bounded(value: object) -> str:
    """An untrusted value (a type from Redis or an old row) as a log line may carry it: escaped, cut short."""
    return repr(value)[:LOG_VALUE_MAX]


def _stored_frame(task_id: uuid.UUID, message: dict[str, Any]) -> str:
    """The frame of a stored event read from the database. One that cannot be sent as it is (a type from
    before ``emit_task_event`` checked it) goes out as ``event.invalid`` with its own ``id`` (ruling R5), so
    the client's ``seq`` stays contiguous and it never reconnects for a gap the server will not fill."""
    frame = format_frame(message)
    if frame is not None:
        return frame
    seq = message["seq"]
    logger.warning(
        "task_stream_event_invalid", task_id=str(task_id), seq=seq, type=_bounded(message.get("type"))
    )
    ts = message.get("ts")
    placeholder = {
        "task_id": str(task_id),
        "seq": seq,
        "type": EVENT_INVALID,
        "ts": ts if isinstance(ts, str) else None,
        "payload": {},
    }
    data = json.dumps(placeholder, ensure_ascii=True, separators=(",", ":"))
    return f"id: {seq}\nevent: {EVENT_INVALID}\ndata: {data}\n\n"


def _live_frame(task_id: uuid.UUID, message: dict[str, Any]) -> str | None:
    """The frame of a message from the hub, or ``None`` (logged): a live message is never trusted further than
    its shape, the database is the record."""
    frame = format_frame(message)
    if frame is None:
        logger.warning(
            "task_stream_event_skipped",
            task_id=str(task_id),
            seq=message.get("seq"),
            type=_bounded(message.get("type")),
        )
    return frame


async def open_stream(env: ProjectEnv, actor: Actor, task_id: uuid.UUID) -> None:
    """The check before a stream starts (the route runs it before the response): ``task:read``, else
    ``not_found()`` for a missing or out-of-scope task, as ``history``."""
    async with env.sessions() as session:
        await _authorize_read(session, actor, task_id)


@contextmanager
def _listen(hub: TaskEventHub | None, task_id: uuid.UUID) -> Iterator[TaskListener | None]:
    """The hub's listener while the hub runs, else ``None``. Without a live subscription now (no hub, a hub
    not started, or one whose Redis is down or not reached yet) the per-ping database check still delivers
    every stored event, only later (spec §13.5 #2); that is logged once per stream."""
    if hub is None or not hub.subscribed:
        state = "absent" if hub is None else "disconnected" if hub.running else "stopped"
        logger.warning("task_stream_without_hub", task_id=str(task_id), hub=state)
    if hub is None or not hub.running:
        yield None
        return
    with hub.listen(task_id) as listener:  # a disconnected hub marks it lagged when it subscribes again
        yield listener


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
    ``cursor`` is the last stored seq sent (as itself or as ``event.invalid``); ``db_may_have_more`` means
    the database may hold events after it; ``status_check_due`` means the task row must be read now;
    ``recheck_at_seq`` is the ``event_seq`` of a finished task whose last events were still unsent, so its
    row is read again once they are. Each pass does the first that applies:

    1. lifetime over -> return (no ``stream.end``: the client reconnects with ``Last-Event-ID``);
    2. ``: ping`` due -> sent; the status check becomes due (it also finds events committed unpublished);
    3. access recheck due (read only) -> task gone -> ``stream.end`` null; session or ``task:read`` lost ->
       return (no ``stream.end``: the reconnect gets 401/404);
    4. ``db_may_have_more`` -> one page of ``seq > cursor`` (own short session), sent in order; more while
       pages are full; a ``task.status_changed`` among them makes the status check due;
    5. status check due (first pass after the replay, after a ping, after a ``task.status_changed`` is sent,
       or once ``cursor`` reaches ``recheck_at_seq``) -> one fresh read of ``status``/``event_seq``: task
       gone -> ``stream.end`` null; finished and ``cursor >= event_seq`` -> ``stream.end`` with that status;
       ``event_seq > cursor`` -> ``db_may_have_more``. ``stream.end`` is only ever sent from a read made in
       that same pass, so a task reopened since an older read never gets one;
    6. listener lagged -> ``db_may_have_more``;
    7. wait for a live message until the next deadline: ``seq`` null -> sent without ``id``; ``seq <=
       cursor`` -> dropped; ``cursor + 1`` -> sent (a ``task.status_changed`` makes the status check due);
       beyond, or not sendable as it is -> ``db_may_have_more`` (the database has it, and any gap before
       it: commit precedes publish).

    No database session is open while a frame is out or while waiting. An unexpected error is logged
    (``task_stream_failed``: task, user, error type) and raised.
    """
    started = clock()
    deadline = started + timings.max_stream_s
    next_ping = started + timings.ping_s
    next_auth = started + timings.auth_recheck_s
    cursor = after_seq
    db_may_have_more = True  # the replay
    status_check_due = True  # right after the replay: a finished task ends without waiting for a ping
    recheck_at_seq: int | None = None
    try:
        with _listen(hub, task_id) as listener:
            while True:
                now = clock()
                if now >= deadline:
                    logger.info("task_stream_closed", task_id=str(task_id), reason="max_lifetime")
                    return
                if now >= next_ping:
                    next_ping = now + timings.ping_s
                    status_check_due = True
                    yield PING_FRAME
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
                if db_may_have_more:
                    page = await _read_page(env, task_id, cursor)
                    db_may_have_more = len(page) == REPLAY_PAGE
                    for message in page:
                        cursor = message["seq"]
                        status_check_due = status_check_due or message["type"] == STATUS_CHANGED
                        yield _stored_frame(task_id, message)
                    continue
                if recheck_at_seq is not None and cursor >= recheck_at_seq:
                    recheck_at_seq = None
                    status_check_due = True
                if status_check_due:
                    status_check_due = False
                    row = await _task_row(env, task_id)
                    if row is None:
                        yield _end_frame(task_id, None)
                        return
                    finished = row.status in FINISHED
                    if finished and cursor >= row.event_seq:
                        yield _end_frame(task_id, row.status)
                        return
                    if row.event_seq > cursor:
                        db_may_have_more = True
                        recheck_at_seq = row.event_seq if finished else None
                    continue
                if listener is not None and listener.take_lagged():
                    db_may_have_more = True
                    continue
                message = await _next_message(listener, min(next_ping, next_auth, deadline) - now)
                if message is None:
                    continue
                seq = message["seq"]
                if seq is None:
                    if (frame := _live_frame(task_id, message)) is not None:
                        yield frame
                elif seq > cursor:
                    frame = _live_frame(task_id, message) if seq == cursor + 1 else None
                    if frame is None:  # a gap before it, or not sendable as it is: the database has the event
                        db_may_have_more = True
                        continue
                    cursor = seq
                    if message["type"] == STATUS_CHANGED:
                        status_check_due = True
                    yield frame
    except Exception as exc:  # the type only: a message may carry a URL or a query
        logger.error(
            "task_stream_failed", task_id=str(task_id), user_id=str(actor.user_id), error=type(exc).__name__
        )
        raise


class StreamSlot:
    """One open stream's place. ``release`` is idempotent: the response's ``finally`` calls it, and so does
    the route when it fails before the response exists; a second call must never free someone else's place."""

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
                headers={"Retry-After": str(STREAMS_RETRY_AFTER_S)},
            )
        self._open[user_id] = self._open.get(user_id, 0) + 1
        return StreamSlot(self, user_id)

    def _free(self, user_id: uuid.UUID) -> None:
        count = self._open.get(user_id, 0) - 1
        if count > 0:
            self._open[user_id] = count
        else:
            self._open.pop(user_id, None)
