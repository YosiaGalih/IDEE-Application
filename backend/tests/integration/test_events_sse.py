"""Task event stream generator: replay, live merge, gap fill, end, lifetime and access (WP-3.2b Task 3).

``stream_frames`` is driven directly (spec WP-3.2 §13.5 #3), with real PostgreSQL and Redis (DB index from
``TEST_REDIS_URL``) and small ``StreamTimings``. Interval names: ``SLOW`` never fires during a test, so an
event that arrives anyway took the path the test is about (live, lagged re-read, ping check).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.events import (
    EventBus,
    RedisEventBus,
    commit_and_publish,
    emit_task_event,
    event_message,
    task_channel,
)
from app.core.ratelimit import FixedWindowLimiter
from app.core.rbac import RoleGrant
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.gitops.adapter import GitAdapter
from app.models.identity import User, UserRole
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from app.services import auth
from app.services import task_events as service
from app.services.projects import Actor, ProjectEnv
from app.services.task_events import (
    EVENT_INVALID,
    STREAM_END,
    StreamTimings,
    TaskEventHub,
    TaskListener,
    format_frame,
    open_stream,
    stream_frames,
)
from app.workflow import states
from tests.conftest import reset_data

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
WAIT = 5.0
SLOW = 60.0  # longer than any test: this interval never fires
PING = ": ping\n\n"
PASSWORD = "stream-long-passphrase"
APP_NAME = "kk-sse-stream-test"  # the stream's own connections, told apart in pg_stat_activity
REDACTOR = Redactor()
AUDIT = AuditWriter(REDACTOR)
SYSTEM = states.Actor("system", "orchestrator")


# --- fixtures ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class World:
    env: ProjectEnv
    settings: Settings
    project_id: uuid.UUID
    task_id: uuid.UUID
    owner: Actor
    writer: async_sessionmaker[Any]  # the test's own sessions, never the stream's


@pytest.fixture
async def stream_engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        migrated_database, connect_args={"server_settings": {"application_name": APP_NAME}}
    )
    yield engine
    await engine.dispose()


@pytest.fixture
async def world(engine: AsyncEngine, stream_engine: AsyncEngine, settings: Settings, tmp_path: Path) -> World:
    await reset_data(engine)
    writer = make_sessionmaker(engine)
    async with writer() as session:
        owner = User(email="owner@example.test", display_name="Tira", password_hash=hash_password(PASSWORD))
        session.add(owner)
        await session.flush()
        session.add(UserRole(user_id=owner.id, role_name="owner", scope="global"))
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.flush()
        task = Task(project_id=project.id, title="t", prompt="p", workflow_type="create", created_by=owner.id)
        session.add(task)
        await session.commit()
    env = ProjectEnv(make_sessionmaker(stream_engine), AUDIT, GitAdapter(), tmp_path)
    actor = Actor(owner.id, owner.email, owner.display_name, (RoleGrant("owner", "global"),))
    return World(env, settings, project.id, task.id, actor, writer)


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = Redis.from_url(REDIS_URL)
    yield client
    await client.aclose()


@pytest.fixture
def bus(redis: Redis) -> EventBus:
    return RedisEventBus(redis)


@pytest.fixture
async def start_hub() -> AsyncIterator[Callable[..., Any]]:
    hubs: list[TaskEventHub] = []

    async def start(queue_max: int = service.HUB_QUEUE_MAX) -> TaskEventHub:
        hub = TaskEventHub(REDIS_URL, queue_max=queue_max)
        hubs.append(hub)
        await hub.start()
        await hub.wait_subscribed(WAIT)
        return hub

    yield start
    for hub in hubs:
        await hub.stop()


@pytest.fixture
async def hub(start_hub: Callable[..., Any]) -> TaskEventHub:
    return await start_hub()


@pytest.fixture
async def streams(world: World) -> AsyncIterator[Callable[..., AsyncGenerator[str, None]]]:
    """Opens streams on the world's task; every one is closed at teardown (its listener leaves the hub)."""
    opened: list[AsyncGenerator[str, None]] = []

    def open_(
        hub: TaskEventHub | None,
        *,
        after_seq: int = 0,
        ping_s: float = SLOW,
        auth_recheck_s: float = SLOW,
        max_stream_s: float = SLOW,
        token: str = "not-checked-within-auth-recheck-s",  # noqa: S107 - no recheck fires
        actor: Actor | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> AsyncGenerator[str, None]:
        generator = stream_frames(
            world.env,
            world.settings,
            hub,
            session_token=token,
            actor=actor or world.owner,
            task_id=world.task_id,
            after_seq=after_seq,
            timings=StreamTimings(ping_s=ping_s, auth_recheck_s=auth_recheck_s, max_stream_s=max_stream_s),
            clock=clock,
        )
        assert isinstance(generator, AsyncGenerator)
        opened.append(generator)
        return generator

    yield open_
    for generator in opened:
        await generator.aclose()


# --- helpers -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    id: int | None
    event: str
    data: Any
    raw: str


def parse(raw: str) -> Frame | None:
    """One frame as the client parses it; ``None`` for a ping. Exactly one blank line, at the end."""
    if raw == PING:
        return None
    assert raw.endswith("\n\n") and "\n\n" not in raw[:-2] and "\r" not in raw, raw
    fields: dict[str, str] = {}
    for line in raw[:-2].split("\n"):
        name, _, value = line.partition(": ")
        assert name in {"id", "event", "data"} and name not in fields, raw
        fields[name] = value
    return Frame(
        int(fields["id"]) if "id" in fields else None, fields["event"], json.loads(fields["data"]), raw
    )


async def next_raw(stream: AsyncGenerator[str, None]) -> str:
    async with asyncio.timeout(WAIT):
        return await anext(stream)


async def next_event(stream: AsyncGenerator[str, None], pings: list[str] | None = None) -> Frame:
    """The next frame that is not a ping (pings seen on the way are appended to ``pings``)."""
    async with asyncio.timeout(WAIT):
        while True:
            raw = await anext(stream)
            frame = parse(raw)
            if frame is not None:
                return frame
            if pings is not None:
                pings.append(raw)


async def ids_until(stream: AsyncGenerator[str, None], last: int) -> list[int | None]:
    ids: list[int | None] = []
    while not ids or ids[-1] != last:
        ids.append((await next_event(stream)).id)
    return ids


async def rest(stream: AsyncGenerator[str, None]) -> list[str]:
    """Every remaining frame; the stream must end on its own within ``WAIT``."""
    async with asyncio.timeout(WAIT):
        return [raw async for raw in stream]


async def add_events(
    world: World, count: int, *, bus: EventBus | None = None, type: str = "task.log", **payload: Any
) -> None:
    """``count`` events, each in its own commit; published only with ``bus`` (else as tool_r3 writes them)."""
    for i in range(count):
        async with world.writer() as session:
            await emit_task_event(session, None, world.task_id, type, {"i": i, **payload}, redactor=REDACTOR)
            if bus is None:
                await session.commit()
            else:
                await commit_and_publish(session, bus)


async def store_raw(world: World, type: str, **payload: Any) -> int:
    """A stored event written past ``emit_task_event`` and its type check (a row from before that check)."""
    async with world.writer() as session:
        seq = (
            await session.execute(
                text("UPDATE tasks SET event_seq = event_seq + 1 WHERE id = :id RETURNING event_seq"),
                {"id": world.task_id},
            )
        ).scalar_one()
        session.add(TaskEvent(task_id=world.task_id, seq=seq, type=type, payload=payload))
        await session.commit()
        return int(seq)


async def stored_message(world: World, seq: int) -> dict[str, Any]:
    async with world.writer() as session:
        row = (
            await session.scalars(
                select(TaskEvent).where(TaskEvent.task_id == world.task_id, TaskEvent.seq == seq)
            )
        ).one()
        return event_message(row.task_id, row.seq, row.type, row.created_at, row.payload)


async def move(world: World, *statuses: states.TaskStatus, bus: EventBus | None = None) -> None:
    async with world.writer() as session:
        task = await session.get(Task, world.task_id)
        assert task is not None
        for status in statuses:
            await states.transition(session, task, status, reason=None, actor=SYSTEM, audit=AUDIT)
        if bus is None:
            await session.commit()
        else:
            await commit_and_publish(session, bus)


def end_frame(world: World, status: str | None) -> str:
    data = json.dumps({"task_id": str(world.task_id), "status": status}, separators=(",", ":"))
    return f"event: {STREAM_END}\ndata: {data}\n\n"


@contextmanager
def spy_listeners(hub: TaskEventHub, monkeypatch: pytest.MonkeyPatch) -> Iterator[list[TaskListener]]:
    listeners: list[TaskListener] = []
    real = hub.listen

    @contextmanager
    def listen(task_id: uuid.UUID) -> Iterator[TaskListener]:
        with real(task_id) as listener:
            listeners.append(listener)
            yield listener

    monkeypatch.setattr(hub, "listen", listen)
    yield listeners


async def until(predicate: Callable[[], Any]) -> None:
    async with asyncio.timeout(WAIT):
        while not predicate():  # noqa: ASYNC110 - polling a plain flag, bounded
            await asyncio.sleep(0.01)


# --- replay and live merge -------------------------------------------------------------------------------


async def test_replay_after_last_event_id(
    world: World, hub: TaskEventHub, bus: EventBus, streams: Any
) -> None:
    await add_events(world, 5)
    stream = streams(hub, after_seq=2)
    for seq in (3, 4, 5):
        raw = await next_raw(stream)
        message = await stored_message(world, seq)
        data = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
        assert raw == f"id: {seq}\nevent: task.log\ndata: {data}\n\n"
    # Live (no ping can fire): the published message is the stored one, byte for byte.
    await add_events(world, 1, bus=bus)
    live = await next_event(stream)
    assert (live.id, live.data) == (6, await stored_message(world, 6))


async def test_no_duplicate_seq(
    world: World, hub: TaskEventHub, bus: EventBus, streams: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "REPLAY_PAGE", 2)
    await add_events(world, 2)
    stream = streams(hub, ping_s=0.05)
    ids = [(await next_event(stream)).id]  # page 1 (seq 1, 2) read; the listener was registered before
    await add_events(world, 2, bus=bus)  # 3, 4: published while the replay still runs
    ids.append((await next_event(stream)).id)
    ids.append((await next_event(stream)).id)  # page 2 (seq 3, 4) read
    await add_events(world, 2, bus=bus)  # 5, 6
    ids += await ids_until(stream, 6)
    assert ids == [1, 2, 3, 4, 5, 6]
    # Never stored, so it can only come through the queue, behind every stale live copy of 3..6: the next
    # frame is this marker only if each of those copies was dropped.
    async with world.writer() as session:
        await emit_task_event(
            session, bus, world.task_id, "agent.delta", {}, ephemeral=True, redactor=REDACTOR
        )
    marker = await next_event(stream)
    assert (marker.id, marker.event) == (None, "agent.delta")
    await add_events(world, 1, bus=bus)
    assert (await next_event(stream)).id == 7


async def test_gap_is_filled_from_the_database(
    world: World, hub: TaskEventHub, bus: EventBus, streams: Any
) -> None:
    await add_events(world, 2)
    stream = streams(hub)
    assert await ids_until(stream, 2) == [1, 2]
    await add_events(world, 1)  # 3: committed, never published
    await add_events(world, 1, bus=bus)  # 4: published, arrives with a gap before it
    third, fourth = await next_event(stream), await next_event(stream)
    assert (third.id, fourth.id) == (3, 4)
    assert third.data == await stored_message(world, 3)
    await add_events(world, 1, bus=bus)
    assert (await next_event(stream)).id == 5


async def test_unpublished_last_event_arrives_on_ping(world: World, hub: TaskEventHub, streams: Any) -> None:
    await add_events(world, 2)
    stream = streams(hub, ping_s=0.05)
    assert await ids_until(stream, 2) == [1, 2]
    await add_events(world, 1)  # the last event, committed without publish (tool_r3, project_delete)
    pings: list[str] = []
    assert (await next_event(stream, pings)).id == 3
    assert pings  # found by the check that follows a ping


async def test_lagged_listener_recovers_from_db(
    world: World, start_hub: Any, bus: EventBus, streams: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = await start_hub(queue_max=1)
    with spy_listeners(hub, monkeypatch) as listeners:
        await add_events(world, 1)
        stream = streams(hub)
        assert (await next_event(stream)).id == 1  # suspended at this frame: nothing reads the queue
        await add_events(world, 20, bus=bus)  # 2..21: one fits, the rest are dropped
        [listener] = listeners
        await until(lambda: listener.lagged)
        assert await ids_until(stream, 21) == list(range(2, 22))
        await add_events(world, 1, bus=bus)
        assert (await next_event(stream)).id == 22


@pytest.mark.parametrize("hub_state", ["none", "not_started"])
async def test_without_hub_delivers_on_ping(
    world: World, bus: EventBus, streams: Any, hub_state: str
) -> None:
    hub = None if hub_state == "none" else TaskEventHub(REDIS_URL)
    await add_events(world, 1)
    with capture_logs() as logs:
        stream = streams(hub, ping_s=0.05)
        assert (await next_event(stream)).id == 1
        await add_events(world, 1, bus=bus)
        pings: list[str] = []
        assert (await next_event(stream, pings)).id == 2
        assert pings
    assert [entry["event"] for entry in logs].count("task_stream_without_hub") == 1


async def test_ephemeral_has_no_id_and_is_not_stored(
    world: World, hub: TaskEventHub, bus: EventBus, redis: Redis, streams: Any
) -> None:
    await add_events(world, 1)
    stream = streams(hub)
    assert (await next_event(stream)).id == 1
    async with world.writer() as session:
        await emit_task_event(
            session,
            bus,
            world.task_id,
            "agent.delta",
            {"text": "sebagian"},
            ephemeral=True,
            redactor=REDACTOR,
        )
    delta = await next_event(stream)
    assert delta.raw.startswith("event: agent.delta\ndata: ")
    assert (delta.id, delta.data["seq"], delta.data["payload"]) == (None, None, {"text": "sebagian"})
    await add_events(world, 1, bus=bus)
    assert (await next_event(stream)).id == 2  # the cursor did not move
    # The hub passes a message without a "seq" key as ephemeral; the stream must survive it too.
    bare = {"task_id": str(world.task_id), "type": "agent.delta", "ts": "2026-11-18T00:00:00Z", "payload": {}}
    await redis.publish(task_channel(world.task_id), json.dumps(bare))
    rebuilt = {
        "task_id": bare["task_id"],
        "seq": None,
        "type": "agent.delta",
        "ts": bare["ts"],
        "payload": {},
    }
    assert (
        await next_event(stream)
    ).raw == f"event: agent.delta\ndata: {json.dumps(rebuilt, separators=(',', ':'))}\n\n"
    await add_events(world, 1, bus=bus)
    assert (await next_event(stream)).id == 3
    async with world.writer() as session:
        stored = await session.scalar(
            select(func.count()).select_from(TaskEvent).where(TaskEvent.task_id == world.task_id)
        )
    assert stored == 3


# --- end, lifetime, access -------------------------------------------------------------------------------


async def test_end_for_finished_task(world: World, hub: TaskEventHub, streams: Any) -> None:
    await add_events(world, 2)
    await move(world, states.TaskStatus.CANCELLED)
    stream = streams(hub)  # no ping fires: the end follows the replay at once
    assert await ids_until(stream, 3) == [1, 2, 3]
    assert await rest(stream) == [end_frame(world, "cancelled")]
    caught_up = streams(hub, after_seq=3)
    assert await rest(caught_up) == [end_frame(world, "cancelled")]


@pytest.mark.parametrize("published", [True, False])
async def test_end_after_live_terminal_transition(
    world: World, hub: TaskEventHub, bus: EventBus, streams: Any, published: bool
) -> None:
    S = states.TaskStatus
    await move(world, S.PLANNED, S.QUEUED, S.RUNNING)
    stream = streams(hub, ping_s=0.05)
    assert await ids_until(stream, 3) == [1, 2, 3]
    await move(world, S.CANCELLED, bus=bus if published else None)
    changed = await next_event(stream)
    assert (changed.id, changed.event, changed.data["payload"]["to"]) == (
        4,
        "task.status_changed",
        "cancelled",
    )
    assert [raw for raw in await rest(stream) if raw != PING] == [end_frame(world, "cancelled")]


async def test_end_when_task_disappears(world: World, hub: TaskEventHub, streams: Any) -> None:
    await add_events(world, 1)
    stream = streams(hub, ping_s=0.05)
    assert (await next_event(stream)).id == 1
    async with world.writer() as session:
        await session.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": world.task_id})
        await session.commit()
    assert [raw for raw in await rest(stream) if raw != PING] == [end_frame(world, None)]


async def test_closes_after_max_lifetime(world: World, hub: TaskEventHub, streams: Any) -> None:
    started = time.monotonic()
    stream = streams(hub, ping_s=0.05, max_stream_s=0.2)
    frames = await rest(stream)
    assert time.monotonic() - started >= 0.2
    assert set(frames) <= {PING}  # closed without stream.end: the client reconnects


async def sign_in(world: World, redis: Redis, email: str) -> tuple[str, Actor, uuid.UUID]:
    """A real session through the login service; ``(token, actor as current_actor builds it, session id)``."""
    sessions = world.env.sessions
    result = await auth.login(
        sessions,
        AUDIT,
        FixedWindowLimiter(redis),
        world.settings,
        email=email,
        password=PASSWORD,
        ip=f"sse-{uuid.uuid4().hex[:12]}",  # its own rate-limit key
        user_agent=None,
        previous_token=None,
    )
    user, session, grants = await auth.resolve_session(sessions, world.settings, result.session_token)
    actor = Actor(user.id, user.email, user.display_name, tuple(grants))
    return result.session_token, actor, session.id


async def add_reader(world: World) -> uuid.UUID:
    async with world.writer() as session:
        reader = User(email="rita@example.test", display_name="Rita", password_hash=hash_password(PASSWORD))
        session.add(reader)
        await session.flush()
        session.add(UserRole(user_id=reader.id, role_name="readonly", scope=f"project:{world.project_id}"))
        await session.commit()
        return reader.id


def count_rechecks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each call of ``resolve_session`` by the stream: one access recheck."""
    calls: list[str] = []
    real = service.resolve_session

    async def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append("recheck")
        return await real(*args, **kwargs)

    monkeypatch.setattr(service, "resolve_session", counted)
    return calls


@pytest.mark.parametrize("revoke", ["logout", "grant_removed"])
async def test_closes_when_access_is_revoked(
    world: World,
    hub: TaskEventHub,
    redis: Redis,
    streams: Any,
    monkeypatch: pytest.MonkeyPatch,
    revoke: str,
) -> None:
    await add_events(world, 1)
    email = "owner@example.test" if revoke == "logout" else "rita@example.test"
    if revoke == "grant_removed":
        await add_reader(world)
    token, actor, session_id = await sign_in(world, redis, email)
    rechecks = count_rechecks(monkeypatch)
    stream = streams(hub, auth_recheck_s=0.05, token=token, actor=actor)
    assert (await next_event(stream)).id == 1
    step = asyncio.create_task(anext(stream))
    await until(lambda: len(rechecks) >= 2)
    assert not step.done()  # rechecks pass while access lasts
    if revoke == "logout":
        await auth.logout(world.env.sessions, AUDIT, session_id=session_id, user_id=actor.user_id, ip=None)
    else:
        async with world.writer() as session:
            await session.execute(text("DELETE FROM user_roles WHERE user_id = :id"), {"id": actor.user_id})
            await session.commit()
    async with asyncio.timeout(WAIT):
        with pytest.raises(StopAsyncIteration):  # closed without stream.end: the reconnect gets 401/404
            await step


async def test_missing_task_ends_before_the_access_check(
    world: World, hub: TaskEventHub, redis: Redis, streams: Any
) -> None:
    token, actor, session_id = await sign_in(world, redis, "owner@example.test")
    stream = streams(hub, auth_recheck_s=0.05, token=token, actor=actor)
    step = asyncio.create_task(anext(stream))
    async with world.writer() as session:  # both at once: no recheck can see only one of them
        await session.execute(
            text("UPDATE sessions SET revoked_at = now() WHERE id = :id"), {"id": session_id}
        )
        await session.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": world.task_id})
        await session.commit()
    async with asyncio.timeout(WAIT):
        first = await step
    assert [first, *await rest(stream)] == [end_frame(world, None)]


async def session_row(world: World, session_id: uuid.UUID) -> tuple[Any, ...]:
    async with world.writer() as session:
        row = (
            await session.execute(
                text("SELECT last_seen_at, revoked_at, expires_at FROM sessions WHERE id = :id"),
                {"id": session_id},
            )
        ).one()
        return tuple(row)


async def test_recheck_leaves_the_session_untouched(
    world: World, hub: TaskEventHub, redis: Redis, streams: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ruling R2: an open stream never extends its session (no last_seen_at write, no revoke commit)."""
    await add_events(world, 1)
    token, actor, session_id = await sign_in(world, redis, "owner@example.test")
    async with world.writer() as session:  # older than LAST_SEEN_RESOLUTION: a touching check would write it
        await session.execute(
            text("UPDATE sessions SET last_seen_at = now() - interval '5 minutes' WHERE id = :id"),
            {"id": session_id},
        )
        await session.commit()
    before = await session_row(world, session_id)
    rechecks = count_rechecks(monkeypatch)
    stream = streams(hub, auth_recheck_s=0.05, token=token, actor=actor)
    assert (await next_event(stream)).id == 1
    step = asyncio.create_task(anext(stream))
    await until(lambda: len(rechecks) >= 2)
    assert not step.done()  # the rechecks passed
    assert await session_row(world, session_id) == before
    step.cancel()
    with pytest.raises(asyncio.CancelledError):
        await step


async def test_recheck_of_an_idle_expired_session_closes_without_revoking(
    world: World, hub: TaskEventHub, redis: Redis, streams: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await add_events(world, 1)
    token, actor, session_id = await sign_in(world, redis, "owner@example.test")
    rechecks = count_rechecks(monkeypatch)
    stream = streams(hub, auth_recheck_s=0.05, token=token, actor=actor)
    assert (await next_event(stream)).id == 1
    step = asyncio.create_task(anext(stream))
    await until(lambda: len(rechecks) >= 1)
    assert not step.done()
    idle = world.settings.session_ttl_hours + 1
    async with world.writer() as session:
        await session.execute(
            text("UPDATE sessions SET last_seen_at = now() - make_interval(hours => :h) WHERE id = :id"),
            {"h": idle, "id": session_id},
        )
        await session.commit()
    async with asyncio.timeout(WAIT):
        with pytest.raises(StopAsyncIteration):  # closed without stream.end
            await step
    assert (await session_row(world, session_id))[
        1
    ] is None  # the next HTTP request revokes it, not the stream


async def test_recheck_is_not_starved_by_a_long_replay(
    world: World, hub: TaskEventHub, redis: Redis, streams: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "REPLAY_PAGE", 2)
    await add_events(world, 20)
    token, actor, session_id = await sign_in(world, redis, "owner@example.test")
    now = [0.0]  # the stream's clock, moved by hand: no recheck is due until the test says so
    stream = streams(hub, auth_recheck_s=0.05, token=token, actor=actor, clock=lambda: now[0])
    assert (await next_event(stream)).id == 1  # page 1 (seq 1, 2) read; 18 events still to replay
    await auth.logout(world.env.sessions, AUDIT, session_id=session_id, user_id=actor.user_id, ip=None)
    now[0] = 1.0
    remaining = [frame for frame in (parse(raw) for raw in await rest(stream)) if frame is not None]
    assert [frame.id for frame in remaining] == [2]  # the page in hand, then the recheck closed the stream


@pytest.mark.parametrize("stop", ["aclose", "cancel"])
async def test_listener_leaves_the_hub_when_the_stream_stops(
    world: World, hub: TaskEventHub, redis: Redis, streams: Any, monkeypatch: pytest.MonkeyPatch, stop: str
) -> None:
    await add_events(world, 1)
    with spy_listeners(hub, monkeypatch) as listeners:
        stream = streams(hub)
        assert (await next_event(stream)).id == 1
        [listener] = listeners
        if stop == "aclose":
            await stream.aclose()
        else:
            step = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)  # one turn: the step runs into its wait for a live message
            step.cancel()
            with pytest.raises(asyncio.CancelledError):
                await step
        other = uuid.uuid4()
        with hub.listen(other) as marker:
            message = {"task_id": str(world.task_id), "seq": 2, "type": "t", "ts": "x", "payload": {}}
            await redis.publish(task_channel(world.task_id), json.dumps(message))
            await redis.publish(task_channel(other), json.dumps({**message, "task_id": str(other)}))
            async with asyncio.timeout(WAIT):
                await marker.queue.get()  # after the stream's message: the hub has routed both
    assert listener.queue.empty() and not listener.lagged


@pytest.mark.parametrize("field", ["ping_s", "auth_recheck_s", "max_stream_s"])
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_stream_timings_must_be_positive(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        StreamTimings(**{field: value})


def test_default_stream_timings() -> None:
    assert StreamTimings() == StreamTimings(ping_s=15, auth_recheck_s=60, max_stream_s=3300)


async def test_open_stream_checks_scope(world: World) -> None:
    await open_stream(world.env, world.owner, world.task_id)
    stranger = Actor(uuid.uuid4(), "eve@example.test", "Eve", ())
    for actor, task_id in ((stranger, world.task_id), (world.owner, uuid.uuid4())):
        with pytest.raises(AppError) as caught:
            await open_stream(world.env, actor, task_id)
        assert (caught.value.code, caught.value.http_status) == ("NOT_FOUND", 404)


# --- frame shape and resources ---------------------------------------------------------------------------


async def test_frame_cannot_be_split(world: World, hub: TaskEventHub, streams: Any) -> None:
    await add_events(world, 1, text="a\n\nid: 99\nevent: x\r\ndata: {}\rb")
    bad_type = "Bad Type\nid: 7"
    await store_raw(world, bad_type)  # 2: a type the event: line cannot carry
    await add_events(world, 1, text="\u2028 tetap satu baris")
    with capture_logs() as logs:
        stream = streams(hub)
        first, second, third = [await next_event(stream) for _ in range(3)]
    assert (first.id, second.id, third.id) == (1, 2, 3)  # 2 keeps its place: the client sees no gap
    assert first.data == await stored_message(world, 1)
    assert first.data["payload"]["text"] == "a\n\nid: 99\nevent: x\r\ndata: {}\rb"
    stored = await stored_message(world, 2)
    placeholder = {
        "task_id": str(world.task_id),
        "seq": 2,
        "type": EVENT_INVALID,
        "ts": stored["ts"],
        "payload": {},
    }
    assert second.raw == (
        f"id: 2\nevent: {EVENT_INVALID}\ndata: {json.dumps(placeholder, separators=(',', ':'))}\n\n"
    )
    assert third.data == await stored_message(world, 3)  # U+2028 is no SSE line break: sent as it is
    invalid = [entry for entry in logs if entry["event"] == "task_stream_event_invalid"]
    assert [(entry["seq"], entry["type"]) for entry in invalid] == [(2, repr(bad_type))]
    message = {"task_id": str(world.task_id), "seq": 1, "ts": "t", "payload": {}}
    for bad in ("Bad Type", "", "a\nb", "x" * 61, "1abc", "a b", None, STREAM_END, EVENT_INVALID):
        assert format_frame({**message, "type": bad}) is None
    for bad_seq in ("1\nevent: x", True, 1.5):
        assert format_frame({**message, "type": "task.log", "seq": bad_seq}) is None
    for number in (float("nan"), float("inf"), float("-inf")):  # not JSON: the client could not parse it
        assert format_frame({**message, "type": "task.log", "payload": {"x": number}}) is None
    assert format_frame({**message, "type": "a" + "b" * 59}) is not None


async def test_published_messages_cannot_end_or_fake_the_stream(
    world: World, hub: TaskEventHub, redis: Redis, streams: Any
) -> None:
    """Redis is a live channel, not the record: what cannot be sent as it is never reaches the client."""
    await add_events(world, 1)
    stream = streams(hub)
    assert (await next_event(stream)).id == 1
    channel = task_channel(world.task_id)
    base = {"task_id": str(world.task_id), "seq": None, "ts": "2026-11-18T00:00:00Z", "payload": {}}
    with capture_logs() as logs:
        for type_ in (STREAM_END, EVENT_INVALID, "Bad Type", "x" * 5000):
            await redis.publish(channel, json.dumps({**base, "type": type_}))
        await redis.publish(
            channel, json.dumps({**base, "type": "agent.delta", "payload": {"x": float("nan")}})
        )
        await add_events(world, 1)  # 2: stored, never published
        await redis.publish(channel, json.dumps({**base, "seq": 2, "type": STREAM_END}))  # claims seq 2
        await redis.publish(channel, json.dumps({**base, "type": "agent.delta"}))  # the marker
        second, marker = await next_event(stream), await next_event(stream)
    assert (second.id, second.event, second.data) == (2, "task.log", await stored_message(world, 2))
    assert (marker.id, marker.event) == (None, "agent.delta")  # the stream did not end
    skipped = [entry for entry in logs if entry["event"] == "task_stream_event_skipped"]
    assert [entry["seq"] for entry in skipped] == [None, None, None, None, None, 2]
    assert all(isinstance(entry["type"], str) and len(entry["type"]) <= 80 for entry in skipped)
    assert skipped[3]["type"].startswith("'xxx")


async def busy_stream_backends(engine: AsyncEngine) -> list[str]:
    """States of the stream's own connections other than ``idle`` (in the pool, no transaction)."""
    async with engine.connect() as probe:
        states_ = await probe.scalars(
            text(
                "SELECT state FROM pg_stat_activity WHERE datname = current_database() "
                "AND application_name = :app AND state <> 'idle'"
            ),
            {"app": APP_NAME},
        )
        return list(states_.all())


async def test_no_db_session_held_between_pings(
    world: World,
    engine: AsyncEngine,
    hub: TaskEventHub,
    bus: EventBus,
    streams: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "REPLAY_PAGE", 2)
    await add_events(world, 3)
    # Suspended at every frame (replay pages, live) the stream holds no connection.
    stream = streams(hub)
    for seq in (1, 2, 3):
        assert (await next_event(stream)).id == seq
        assert await busy_stream_backends(engine) == []
    # While it waits for the next message no connection of the stream is busy (none idle in transaction).
    step = asyncio.create_task(anext(stream))
    async with asyncio.timeout(WAIT):
        while await busy_stream_backends(engine):  # noqa: ASYNC110 - polling the database, bounded
            await asyncio.sleep(0.01)
    assert not step.done()  # observed during the wait
    await add_events(world, 1, bus=bus)
    async with asyncio.timeout(WAIT):
        live = parse(await step)
    assert live is not None and live.id == 4
    # Across ping checks: at every ping and every frame the check's session is already closed.
    pinging = streams(hub, after_seq=4, ping_s=0.05)
    for _ in range(4):
        assert await next_raw(pinging) == PING
        assert await busy_stream_backends(engine) == []


async def test_lone_surrogate_in_an_ephemeral_payload_streams_as_ascii(
    world: World, hub: TaskEventHub, redis: Redis, streams: Any
) -> None:
    """Ruling R4: ``\\ud800`` in a published payload must not break the UTF-8 encoding of the response."""
    stream = streams(hub)
    await add_events(world, 1)
    assert (await next_event(stream)).id == 1
    bare = {
        "task_id": str(world.task_id),
        "type": "agent.delta",
        "ts": "2026-11-18T00:00:00Z",
        "payload": {"text": "a\ud800b \u00e9"},
    }
    await redis.publish(task_channel(world.task_id), json.dumps(bare))
    raw = await next_raw(stream)
    assert raw.isascii()
    raw.encode("utf-8")
    assert parse(raw).data["payload"]["text"] == "a\ud800b \u00e9"  # type: ignore[union-attr]
