"""``GET /tasks/{id}/events`` over HTTP and the stream slots (WP-3.2b Task 4).

``ASGITransport`` buffers the whole body, so only streams that end (a ``FINISHED`` task) and errors before
the stream are driven over HTTP; the slot logic is driven directly (spec WP-3.2 §13.3).
"""

# ruff: noqa: F811  # the shared fixtures are imported by name and redefined as test arguments

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import StreamingResponse

from app.api.routes.tasks import stream_response
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.events import emit_task_event
from app.core.redaction import Redactor
from app.services.task_events import STREAM_END, StreamSlots
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
    assert response.json()["error"]["details"][0]["loc"] == loc
    assert response.headers["content-type"].startswith("application/json")


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


async def test_slot_released_when_client_never_reads() -> None:
    slots = StreamSlots(per_user=1, total=1)
    me = uuid.uuid4()

    async def never() -> None:
        raise AssertionError("never iterated")
        yield ""  # pragma: no cover

    response = stream_response(slots.acquire(me), never(), request_id="r")
    too_many(slots, me)
    assert isinstance(response, StreamingResponse) and response.background is not None
    await response.background()  # what Starlette runs after the response, read or not
    slots.acquire(me)


async def test_slot_released_when_stream_ends() -> None:
    slots = StreamSlots(per_user=1, total=1)
    me = uuid.uuid4()

    async def two():  # type: ignore[no-untyped-def]
        yield "a"
        yield "b"

    response = stream_response(slots.acquire(me), two(), request_id="r")
    assert [chunk async for chunk in response.body_iterator] == ["a", "b"]
    slots.acquire(me)  # free before the background task even ran


async def test_slot_released_when_stream_fails_midway() -> None:
    slots = StreamSlots(per_user=1, total=1)
    me = uuid.uuid4()

    async def boom():  # type: ignore[no-untyped-def]
        yield "a"
        raise RuntimeError("x")

    response = stream_response(slots.acquire(me), boom(), request_id="r")
    with pytest.raises(RuntimeError):
        [chunk async for chunk in response.body_iterator]
    slots.acquire(me)
