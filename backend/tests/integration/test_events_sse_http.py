"""``GET /tasks/{id}/events`` over HTTP, its response's lifecycle and the stream slots (WP-3.2b Task 4).

``ASGITransport`` buffers the whole body, so only streams that end (a ``FINISHED`` task) and errors before
the stream are driven over HTTP (spec WP-3.2 §13.3); ``TaskEventResponse`` is driven as an ASGI app with a
hand-written ``send``/``receive`` for the rest: a client gone before or during the body, one that stops
reading.
"""

# ruff: noqa: F811  # the shared fixtures are imported by name and redefined as test arguments

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import Message
from structlog.testing import capture_logs

from app.api.routes import tasks as tasks_route
from app.api.routes.tasks import TaskEventResponse
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.events import emit_task_event
from app.core.redaction import Redactor
from app.services.task_events import (
    AUTH_RECHECK_S,
    MAX_STREAM_S,
    MAX_STREAMS_PER_USER,
    PING_INTERVAL_S,
    STREAM_END,
    StreamSlots,
    StreamTimings,
    TaskEventHub,
    jittered_timings,
    stream_frames,
)
from app.workflow import states
from tests.integration.test_events_sse import (  # noqa: F401 - fixtures shared with the generator tests
    SLOW,
    WAIT,
    World,
    add_events,
    hub,
    move,
    start_hub,
    stream_engine,
    world,
)
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    make_user,
    owner,
    owner_id,
    signed_in,
    workspace_root,
)
from tests.integration.test_tasks_api import PASSWORD, make_tasks, open_gate, project  # noqa: F401


async def finished_task(client: httpx.AsyncClient, engine: AsyncEngine, events: int = 3) -> str:
    project_id = await project(client)
    [task_id] = await make_tasks(client, project_id, "alir")
    async with make_sessionmaker(engine)() as session:
        for i in range(events):
            await emit_task_event(
                session, None, uuid.UUID(task_id), "task.log", {"i": i}, redactor=Redactor()
            )
        await session.commit()
    async with engine.begin() as conn:  # the status guard accepts only workflow.states.transition's marker
        await conn.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
        await conn.execute(
            text("UPDATE tasks SET status = 'cancelled' WHERE id = :id"), {"id": uuid.UUID(task_id)}
        )
    return task_id


def frames(body: str) -> list[str]:
    return [f for f in body.split("\n\n") if f]


def ids(body: str) -> list[int]:
    return [int(f.split("\n")[0][4:]) for f in frames(body) if f.startswith("id: ")]


async def test_stream_of_finished_task_over_http(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    task_id = await finished_task(owner, engine)
    response = await owner.get(f"/api/v1/tasks/{task_id}/events")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-request-id"]
    got = frames(response.text)
    assert ids(response.text) == [1, 2, 3]
    assert got[-1].startswith(f"event: {STREAM_END}\n")
    assert json.loads(got[-1].split("data: ")[1]) == {"task_id": task_id, "status": "cancelled"}


async def test_last_event_id_wins_over_after_seq(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    task_id = await finished_task(owner, engine, events=4)
    response = await owner.get(
        f"/api/v1/tasks/{task_id}/events", params={"after_seq": 0}, headers={"Last-Event-ID": "2"}
    )
    assert ids(response.text) == [3, 4]
    plain = await owner.get(f"/api/v1/tasks/{task_id}/events", params={"after_seq": 3})
    assert ids(plain.text) == [4]


@pytest.mark.parametrize(
    ("header", "query", "loc"),
    [
        ("abc", None, ["header", "Last-Event-ID"]),
        ("-1", None, ["header", "Last-Event-ID"]),
        (str(2**63), None, ["header", "Last-Event-ID"]),
        ("1.5", None, ["header", "Last-Event-ID"]),
        (None, "abc", ["query", "after_seq"]),
        (None, "-1", ["query", "after_seq"]),
        (None, str(2**63), ["query", "after_seq"]),
        ("1" * 41, None, ["header", "Last-Event-ID"]),  # longer than any seq: still our own 422 shape
        (None, "1" * 41, ["query", "after_seq"]),
        ("0" * 20, None, ["header", "Last-Event-ID"]),  # 0..2**63-1 is at most 19 digits
        (None, "", ["query", "after_seq"]),
    ],
)
async def test_bad_resume_point_is_422(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    header: str | None,
    query: str | None,
    loc: list[str],
) -> None:
    task_id = await finished_task(owner, engine)
    response = await owner.get(
        f"/api/v1/tasks/{task_id}/events",
        params={"after_seq": query} if query is not None else None,
        headers={"Last-Event-ID": header} if header is not None else None,
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"] == [  # one shape for every bad value: _resume_point's own
        {"loc": loc, "msg": "must be an integer from 0 to 9223372036854775807"}
    ]
    assert response.headers["content-type"].startswith("application/json")


async def test_empty_last_event_id_counts_as_absent(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    """Ruling R6: a client may send the header empty on its first connect; ``after_seq`` then applies."""
    task_id = await finished_task(owner, engine, events=4)
    response = await owner.get(
        f"/api/v1/tasks/{task_id}/events", params={"after_seq": 2}, headers={"Last-Event-ID": ""}
    )
    assert response.status_code == 200, response.text
    assert ids(response.text) == [3, 4]
    bare = await owner.get(f"/api/v1/tasks/{task_id}/events", headers={"Last-Event-ID": ""})
    assert ids(bare.text) == [1, 2, 3, 4]


async def test_too_many_streams_over_http(
    app: FastAPI,
    owner: httpx.AsyncClient,
    owner_id: uuid.UUID,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    task_id = await finished_task(owner, engine)
    slots: StreamSlots = app.state.dependencies.stream_slots
    held = [slots.acquire(owner_id) for _ in range(MAX_STREAMS_PER_USER)]
    try:
        refused = await owner.get(f"/api/v1/tasks/{task_id}/events")
        unknown = await owner.get(f"/api/v1/tasks/{uuid.uuid4()}/events")  # 404 comes first
        bad = await owner.get(f"/api/v1/tasks/{task_id}/events", headers={"Last-Event-ID": "x"})  # then 422
    finally:
        for slot in held:
            slot.release()
    assert refused.status_code == 429, refused.text
    assert refused.headers["content-type"].startswith("application/json")
    assert refused.headers["retry-after"] == "5"
    error = refused.json()["error"]
    assert error["code"] == "RATE_LIMITED" and error["details"][0]["reason"] == "too_many_streams"
    assert (unknown.status_code, bad.status_code) == (404, 422)
    assert (await owner.get(f"/api/v1/tasks/{task_id}/events")).status_code == 200  # nothing leaked


async def test_route_jitters_the_lifetime(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = await finished_task(owner, engine)
    seen: list[StreamTimings] = []
    real = tasks_route.stream_frames

    def spy(*args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        seen.append(kwargs["timings"])
        return real(*args, **kwargs)

    monkeypatch.setattr(tasks_route, "stream_frames", spy)
    for _ in range(3):
        assert (await owner.get(f"/api/v1/tasks/{task_id}/events")).status_code == 200
    assert all(0.9 * MAX_STREAM_S <= t.max_stream_s <= MAX_STREAM_S for t in seen) and len(seen) == 3


def test_lifetime_jitter_only_shortens_a_stream() -> None:
    """Ruling R7: up to 10 % shorter, never longer (spec #31: at most 55 minutes, under Caddy's hour)."""
    assert jittered_timings(lambda: 0.0).max_stream_s == pytest.approx(0.9 * MAX_STREAM_S)
    assert jittered_timings(lambda: 0.5).max_stream_s == pytest.approx(0.95 * MAX_STREAM_S)
    assert jittered_timings(lambda: 1.0).max_stream_s == pytest.approx(MAX_STREAM_S)
    lifetimes = [jittered_timings().max_stream_s for _ in range(200)]
    assert all(0.9 * MAX_STREAM_S <= lifetime <= MAX_STREAM_S for lifetime in lifetimes)
    assert len(set(lifetimes)) > 1  # spread: streams opened together do not reconnect together
    assert MAX_STREAM_S <= 3300  # 55 minutes: with the grace the response stays under the hour
    timings = jittered_timings()
    assert (timings.ping_s, timings.auth_recheck_s) == (PING_INTERVAL_S, AUTH_RECHECK_S)


async def test_stream_requires_session_and_scope(
    app,  # type: ignore[no-untyped-def]
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    task_id = await finished_task(owner, engine)
    anonymous = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    try:
        denied = await anonymous.get(f"/api/v1/tasks/{task_id}/events")
        assert denied.status_code == 401 and denied.json()["error"]["code"] == "UNAUTHENTICATED"
    finally:
        await anonymous.aclose()
    await make_user(engine, "eve@example.test")
    eve = await signed_in(app, "eve@example.test", PASSWORD)
    try:
        hidden = await eve.get(f"/api/v1/tasks/{task_id}/events")
        random = await eve.get(f"/api/v1/tasks/{uuid.uuid4()}/events")
        assert hidden.status_code == 404 and random.status_code == 404
        assert {**hidden.json(), "request_id": None} == {**random.json(), "request_id": None}
        assert hidden.headers["content-type"].startswith("application/json")
    finally:
        await eve.aclose()


async def last_seen(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    async with engine.connect() as conn:
        return (
            await conn.execute(text("SELECT last_seen_at FROM sessions ORDER BY created_at DESC LIMIT 1"))
        ).scalar()


async def test_the_stream_does_not_touch_the_session(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    """Ruling R3: an automatic reconnect is not user activity; a normal request still is."""
    task_id = await finished_task(owner, engine)
    old = "now() - interval '2 minutes'"
    async with engine.begin() as conn:
        await conn.execute(text(f"UPDATE sessions SET last_seen_at = {old}"))  # noqa: S608
    before = await last_seen(engine)
    for _ in range(2):  # open and reconnect
        assert (await owner.get(f"/api/v1/tasks/{task_id}/events")).status_code == 200
    assert await last_seen(engine) == before
    assert (await owner.get(f"/api/v1/tasks/{task_id}")).status_code == 200
    assert await last_seen(engine) > before


# --- slots ------------------------------------------------------------


def too_many(slots: StreamSlots, user: uuid.UUID) -> AppError:
    with pytest.raises(AppError) as caught:
        slots.acquire(user)
    return caught.value


def test_too_many_streams_is_429() -> None:
    slots = StreamSlots(per_user=2, total=3)
    me, other = uuid.uuid4(), uuid.uuid4()
    first, _ = slots.acquire(me), slots.acquire(me)
    error = too_many(slots, me)
    assert (error.code, error.http_status) == ("RATE_LIMITED", 429)
    assert error.headers == {"Retry-After": "5"}
    assert error.details is not None and error.details[0]["reason"] == "too_many_streams"
    slots.acquire(other)  # another user is not affected by the per-user limit ...
    assert too_many(slots, other).details[0]["reason"] == "too_many_streams"  # type: ignore[index]  # ... but the total cap
    first.release()
    slots.acquire(other)  # a released slot is free again


def test_release_is_idempotent() -> None:
    slots = StreamSlots(per_user=1, total=1)
    me = uuid.uuid4()
    slot = slots.acquire(me)
    slot.release()
    slot.release()  # a second release must not free someone else's slot
    again = slots.acquire(me)
    slot.release()
    too_many(slots, me)
    again.release()
    slots.acquire(me)


async def no_disconnect() -> Message:
    await asyncio.Event().wait()  # an ASGI 2.4 server never needs receive() for a streaming response
    raise AssertionError("unreachable")


def asgi(spec: str) -> dict[str, Any]:
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": spec}}


def response_of(
    frames: AsyncGenerator[str, None], slots: StreamSlots, *, deadline_s: float = WAIT
) -> tuple[TaskEventResponse, uuid.UUID]:
    me = uuid.uuid4()
    task_id = uuid.uuid4()
    slot = slots.acquire(me)
    return TaskEventResponse(
        frames, slot, task_id=task_id, request_id="req_test1234", deadline_s=deadline_s
    ), me


async def test_slot_released_when_stream_ends() -> None:
    slots = StreamSlots(per_user=1, total=1)

    async def two() -> AsyncGenerator[str, None]:
        yield "a"
        yield "b"

    response, me = response_of(two(), slots)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await response(asgi("2.4"), no_disconnect, send)
    assert [m.get("body") for m in sent[1:]] == [b"a", b"b", b""]
    assert dict(sent[0]["headers"])[b"cache-control"] == b"no-store"
    slots.acquire(me)  # free


async def test_slot_released_when_stream_fails_midway() -> None:
    slots = StreamSlots(per_user=1, total=1)

    async def boom() -> AsyncGenerator[str, None]:
        yield "a"
        raise RuntimeError("x")

    response, me = response_of(boom(), slots)

    async def send(message: Message) -> None:
        pass

    with pytest.raises(RuntimeError):
        await response(asgi("2.4"), no_disconnect, send)
    slots.acquire(me)


# --- the response's lifecycle with a real stream and hub (spec §13.5 #6; AG-13 LOW-1, LOW-2) ------------


def real_stream(world: World, hub: TaskEventHub, **timings: float) -> AsyncGenerator[str, None]:
    return stream_frames(
        world.env,
        world.settings,
        hub,
        session_token="not-checked",  # no recheck fires
        actor=world.owner,
        task_id=world.task_id,
        after_seq=0,
        timings=StreamTimings(**{"ping_s": SLOW, "auth_recheck_s": SLOW, "max_stream_s": SLOW, **timings}),
    )


def listening(hub: TaskEventHub, task_id: uuid.UUID) -> bool:
    return task_id in hub._listeners  # pyright: ignore[reportPrivateUsage]


def lifecycle(world: World, hub: TaskEventHub, *, deadline_s: float = WAIT, **timings: float) -> Any:
    slots = StreamSlots(per_user=1, total=1)
    frames = real_stream(world, hub, **timings)
    slot = slots.acquire(world.owner.user_id)
    response = TaskEventResponse(
        frames, slot, task_id=world.task_id, request_id="req_test1234", deadline_s=deadline_s
    )
    return response, frames, slots


async def assert_released(world: World, hub: TaskEventHub, frames: Any, slots: StreamSlots) -> None:
    slots.acquire(world.owner.user_id)  # the slot is free
    assert not listening(hub, world.task_id)  # the generator is closed: its listener left the hub
    with pytest.raises(StopAsyncIteration):
        await anext(frames)


@pytest.mark.parametrize("gone_at", ["http.response.start", "http.response.body"])
async def test_client_gone_under_asgi_2_4_frees_slot_and_listener(
    world: World, hub: TaskEventHub, gone_at: str
) -> None:
    """ASGI 2.4: a failed send raises ClientDisconnect in Starlette, which then skips any background task.
    For an event stream that is the normal end: logged, not raised as an unhandled error."""
    await add_events(world, 1)
    response, frames, slots = lifecycle(world, hub)
    seen: list[str] = []

    async def send(message: Message) -> None:
        if message["type"] == gone_at:
            seen.append(gone_at)
            if gone_at == "http.response.body":
                assert listening(hub, world.task_id)  # the stream is running when the client goes
            raise OSError("client gone")

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        async with asyncio.timeout(WAIT):
            await response(asgi("2.4"), no_disconnect, send)
    assert seen == [gone_at]
    await assert_released(world, hub, frames, slots)
    closed = [entry for entry in logs if entry["event"] == "task_stream_closed"]
    assert [(e["reason"], e["task_id"], e["request_id"]) for e in closed] == [
        ("client_gone", str(world.task_id), "req_test1234")
    ]


async def test_client_that_stops_reading_is_released_after_the_deadline(
    world: World, hub: TaskEventHub
) -> None:
    await add_events(world, 1)
    response, frames, slots = lifecycle(world, hub, deadline_s=0.3, max_stream_s=0.2)

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            await asyncio.Event().wait()  # the TCP buffer is full and the client never reads

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        async with asyncio.timeout(WAIT):
            await response(asgi("2.4"), no_disconnect, send)
    await assert_released(world, hub, frames, slots)
    closed = [entry for entry in logs if entry["event"] == "task_stream_closed"]
    assert [(e["reason"], e["task_id"], e["request_id"]) for e in closed] == [
        ("send_timeout", str(world.task_id), "req_test1234")
    ]


async def test_normal_end_frees_slot_and_listener(world: World, hub: TaskEventHub) -> None:
    await add_events(world, 1)
    await move(world, states.TaskStatus.CANCELLED)
    response, frames, slots = lifecycle(world, hub)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async with asyncio.timeout(WAIT):
        await response(asgi("2.4"), no_disconnect, send)
    bodies = b"".join(m.get("body", b"") for m in sent[1:]).decode()
    assert bodies.endswith(
        f'event: {STREAM_END}\ndata: {{"task_id":"{world.task_id}","status":"cancelled"}}\n\n'
    )
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
    await assert_released(world, hub, frames, slots)


async def test_drop_mid_stream_frees_slot_and_listener(world: World, hub: TaskEventHub) -> None:
    """ASGI 2.0-2.3: Starlette listens for http.disconnect and cancels the body."""
    await add_events(world, 1)
    response, frames, slots = lifecycle(world, hub)
    first_body = asyncio.Event()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_body.set()

    async def receive() -> Message:
        await first_body.wait()
        return {"type": "http.disconnect"}

    async with asyncio.timeout(WAIT):
        await response(asgi("2.3"), receive, send)
    await assert_released(world, hub, frames, slots)


async def test_failure_is_logged_with_the_request_id(world: World, hub: TaskEventHub) -> None:
    calls: list[int] = []

    def sessions() -> Any:
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("database gone")
        return world.env.sessions()

    world = dataclasses.replace(world, env=dataclasses.replace(world.env, sessions=sessions))  # type: ignore[arg-type]
    await add_events(world, 1)
    response, frames, slots = lifecycle(world, hub)

    async def send(message: Message) -> None:
        pass

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        with pytest.raises(RuntimeError):
            async with asyncio.timeout(WAIT):
                await response(asgi("2.4"), no_disconnect, send)
    failed = [entry for entry in logs if entry["event"] == "task_stream_failed"]
    assert [(e["task_id"], e["request_id"], e["error"]) for e in failed] == [
        (str(world.task_id), "req_test1234", "RuntimeError")
    ]
    await assert_released(world, hub, frames, slots)
