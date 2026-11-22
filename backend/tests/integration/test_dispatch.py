"""Orphan re-dispatch sweep (spec WP-3.2 §14 #41, rulings R-T4a, R-T4b): fake dispatcher, real database."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.core.db import make_sessionmaker
from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.workflow import dispatch as dispatch_module
from app.workflow.dispatch import DISPATCH_GRACE_S, DispatchResult, redispatch_orphans
from tests.conftest import reset_data

OLD = timedelta(seconds=DISPATCH_GRACE_S * 5)
MAX_ACTIVE = 100  # generous: capacity is not what these tests look at


def at(age: timedelta) -> str:
    return (datetime.now(UTC) - age).isoformat()


class RecordingDispatcher:
    def __init__(self, fail: set[uuid.UUID] | None = None, raises: BaseException | None = None) -> None:
        self.sent: list[uuid.UUID] = []
        self._fail = fail or set()
        self._raises = raises

    async def enqueue_task(self, task_id: uuid.UUID) -> None:
        if self._raises is not None:
            raise self._raises
        if task_id in self._fail:
            raise RuntimeError("broker secret-host:6379 refused")
        self.sent.append(task_id)


@pytest.fixture
async def make_task(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    await reset_data(engine)
    async with make_sessionmaker(engine)() as session:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.commit()
        owner_id, project_id = owner.id, project.id

    async def create(
        status: str = "draft",
        checkpoint: dict[str, Any] | None = None,
        *,
        lease_seconds: float | None = None,  # None: no lease; negative: expired
    ) -> uuid.UUID:
        async with make_sessionmaker(engine)() as session:
            task = Task(
                project_id=project_id,
                title="t",
                prompt="p",
                workflow_type="create",
                created_by=owner_id,
                checkpoint=checkpoint or {},
            )
            session.add(task)
            await session.flush()
            await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await session.execute(
                text(
                    "UPDATE tasks SET status = :s, "
                    "lease_owner = CASE WHEN CAST(:l AS double precision) IS NULL THEN NULL ELSE 'w' END, "
                    "lease_expires_at = now() + make_interval(secs => CAST(:l AS double precision)) "
                    "WHERE id = :id"
                ),
                {"s": status, "l": lease_seconds, "id": task.id},
            )
            await session.commit()
            return task.id

    return create


async def checkpoint_of(engine: AsyncEngine, task_id: uuid.UUID) -> dict[str, Any]:
    async with engine.connect() as conn:
        row = await conn.execute(text("SELECT checkpoint FROM tasks WHERE id = :i"), {"i": task_id})
        return row.scalar_one()


async def test_orphans_are_dispatched_once_per_grace(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    draft = await make_task("draft", {"dispatch": {"at": at(OLD), "count": 1}})
    queued = await make_task("queued", {"dispatch": {"at": at(OLD)}}, lease_seconds=-5)
    dispatcher = RecordingDispatcher()
    sessions = make_sessionmaker(engine)

    first = await redispatch_orphans(sessions, dispatcher, max_active=MAX_ACTIVE)
    assert first == DispatchResult(candidates=2, dispatched=2, failed=0, skipped=0)
    assert set(dispatcher.sent) == {draft, queued}
    assert (await checkpoint_of(engine, draft))["dispatch"]["count"] == 2
    assert (await checkpoint_of(engine, queued))["dispatch"]["count"] == 1  # coalesce(missing, 0) + 1

    second = await redispatch_orphans(
        sessions, dispatcher, max_active=MAX_ACTIVE
    )  # dispatch.at just advanced: inside the grace
    assert second == DispatchResult(0, 0, 0, 0)
    assert len(dispatcher.sent) == 2


async def test_live_lease_or_inside_grace_is_left_alone(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("queued", {"dispatch": {"at": at(OLD)}}, lease_seconds=60)
    await make_task("draft", {"dispatch": {"at": at(timedelta(seconds=1))}})
    dispatcher = RecordingDispatcher()
    result = await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=MAX_ACTIVE)
    assert result == DispatchResult(0, 0, 0, 0)
    assert dispatcher.sent == []


async def test_never_dispatched_tasks_are_ignored(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("draft", {})
    await make_task("draft", {"user_set": {"a": 1}})
    # R-T4a: the engine's draft claim can leave a dispatch object that holds only planning_attempts.
    await make_task("queued", {"dispatch": {"planning_attempts": 1}})
    dispatcher = RecordingDispatcher()
    result = await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=MAX_ACTIVE)
    assert result == DispatchResult(0, 0, 0, 0)
    assert dispatcher.sent == []


@pytest.mark.parametrize(
    "status", ["running", "validating", "completed", "failed", "interrupted", "cancelled"]
)
async def test_other_statuses_are_ignored(engine: AsyncEngine, make_task, status: str) -> None:  # type: ignore[no-untyped-def]
    await make_task(status, {"dispatch": {"at": at(OLD)}})
    dispatcher = RecordingDispatcher()
    result = await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=MAX_ACTIVE)
    assert result == DispatchResult(0, 0, 0, 0)
    assert dispatcher.sent == []


async def test_enqueue_error_does_not_stop_the_others(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    bad = await make_task("draft", {"dispatch": {"at": at(OLD + timedelta(seconds=30)), "count": 0}})
    good = await make_task("draft", {"dispatch": {"at": at(OLD), "count": 0}})
    dispatcher = RecordingDispatcher(fail={bad})
    with capture_logs() as logs:
        result = await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=MAX_ACTIVE)
    assert result == DispatchResult(candidates=2, dispatched=1, failed=1, skipped=0)
    assert dispatcher.sent == [good]
    failed_logs = [e for e in logs if e["event"] == "task_redispatch_failed"]
    assert len(failed_logs) == 1
    entry = failed_logs[0]
    assert entry["task_id"] == str(bad)
    assert entry["error_type"] == "RuntimeError"
    assert "exc_info" not in entry
    assert "secret-host" not in str(entry)
    summary = [e for e in logs if e["event"] == "task_redispatch_sweep"]
    assert [
        (e["log_level"], e["candidates"], e["dispatched"], e["failed"], e["skipped"]) for e in summary
    ] == [("warning", 2, 1, 1, 0)]
    # The write committed before the enqueue: the task waits one more grace, not a busy loop.
    assert (await checkpoint_of(engine, bad))["dispatch"]["count"] == 1
    assert (
        await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=MAX_ACTIVE)
    ).candidates == 0


async def test_cancellation_is_not_swallowed(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("draft", {"dispatch": {"at": at(OLD)}})
    with pytest.raises(asyncio.CancelledError):
        await redispatch_orphans(
            make_sessionmaker(engine),
            RecordingDispatcher(raises=asyncio.CancelledError()),
            max_active=MAX_ACTIVE,
        )


async def test_skip_locked_candidate_returns_later(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft", {"dispatch": {"at": at(OLD)}})
    dispatcher = RecordingDispatcher()
    sessions = make_sessionmaker(engine)
    async with sessions() as holder:
        await holder.execute(text("SELECT id FROM tasks WHERE id = :i FOR UPDATE"), {"i": task_id})
        locked = await redispatch_orphans(sessions, dispatcher, max_active=MAX_ACTIVE)
        assert locked == DispatchResult(candidates=1, dispatched=0, failed=0, skipped=1)
        assert dispatcher.sent == []
        await holder.rollback()
    later = await redispatch_orphans(sessions, dispatcher, max_active=MAX_ACTIVE)
    assert later == DispatchResult(candidates=1, dispatched=1, failed=0, skipped=0)
    assert dispatcher.sent == [task_id]


async def test_lease_claimed_between_listing_and_locking_is_skipped(
    engine: AsyncEngine, make_task, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("queued", {"dispatch": {"at": at(OLD)}})
    sessions = make_sessionmaker(engine)
    listed = dispatch_module._list_candidates

    async def list_then_claim(*args: Any, **kwargs: Any) -> list[uuid.UUID]:
        candidates = await listed(*args, **kwargs)
        async with sessions() as db:  # a worker claims the task after the listing, before the row lock
            await db.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await db.execute(
                text(
                    "UPDATE tasks SET lease_owner = 'w', "
                    "lease_expires_at = now() + interval '60 s' WHERE id = :i"
                ),
                {"i": task_id},
            )
            await db.commit()
        return candidates

    monkeypatch.setattr(dispatch_module, "_list_candidates", list_then_claim)
    dispatcher = RecordingDispatcher()
    result = await redispatch_orphans(sessions, dispatcher, max_active=MAX_ACTIVE)
    assert result == DispatchResult(candidates=1, dispatched=0, failed=0, skipped=1)
    assert dispatcher.sent == []


async def test_oldest_first_and_capped_per_sweep(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    newest = await make_task("draft", {"dispatch": {"at": at(OLD)}})
    oldest = await make_task("draft", {"dispatch": {"at": at(OLD * 3)}})
    middle = await make_task("draft", {"dispatch": {"at": at(OLD * 2)}})
    dispatcher = RecordingDispatcher()
    sessions = make_sessionmaker(engine)

    first = await redispatch_orphans(sessions, dispatcher, max_active=MAX_ACTIVE, batch=2)
    assert first.candidates == 2
    assert dispatcher.sent == [oldest, middle]

    second = await redispatch_orphans(
        sessions, dispatcher, max_active=MAX_ACTIVE, batch=2
    )  # the first two are inside the grace now
    assert second.candidates == 1
    assert dispatcher.sent == [oldest, middle, newest]


async def test_dispatch_write_keeps_the_rest_of_the_checkpoint(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task(
        "draft",
        {
            "dispatch": {"at": at(OLD), "count": 4, "planning_attempts": 2},
            "user_set": {"k": "v"},
            "completed_steps": ["intake"],
        },
    )
    await redispatch_orphans(make_sessionmaker(engine), RecordingDispatcher(), max_active=MAX_ACTIVE)
    checkpoint = await checkpoint_of(engine, task_id)
    assert checkpoint["user_set"] == {"k": "v"}
    assert checkpoint["completed_steps"] == ["intake"]
    assert checkpoint["dispatch"]["planning_attempts"] == 2
    assert checkpoint["dispatch"]["count"] == 5
    assert datetime.fromisoformat(checkpoint["dispatch"]["at"]) > datetime.now(UTC) - timedelta(seconds=30)


async def test_live_leases_reduce_the_batch(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("running", lease_seconds=60)  # any status counts, as claim_lease does
    await make_task("queued", {"dispatch": {"at": at(OLD)}}, lease_seconds=60)
    for age in (OLD * 3, OLD * 2, OLD):
        await make_task("draft", {"dispatch": {"at": at(age)}})
    dispatcher = RecordingDispatcher()

    result = await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=3)

    assert result == DispatchResult(candidates=1, dispatched=1, failed=0, skipped=0)
    assert len(dispatcher.sent) == 1


async def test_in_flight_tasks_reduce_the_batch(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    recent = {"dispatch": {"at": at(timedelta(seconds=1))}}
    await make_task("queued", recent)  # sent inside the grace, not claimed yet
    await make_task("draft", recent, lease_seconds=-5)
    for age in (OLD * 3, OLD * 2, OLD):
        await make_task("draft", {"dispatch": {"at": at(age)}})
    dispatcher = RecordingDispatcher()

    result = await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=3)

    assert result == DispatchResult(candidates=1, dispatched=1, failed=0, skipped=0)


async def test_what_is_neither_live_nor_in_flight_takes_no_capacity(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    """Review T6: a dead lease without dispatch.at, a recent dispatch already past the waiting statuses, and
    finished history count for nothing; only the 3 orphans are left, so all 3 go out."""
    recent = {"dispatch": {"at": at(timedelta(seconds=1))}}
    await make_task("draft", lease_seconds=-5)  # never dispatched
    await make_task("running", recent, lease_seconds=-5)  # dispatched recently, but no longer waiting
    await make_task("completed", recent)
    for age in (OLD * 3, OLD * 2, OLD):
        await make_task("draft", {"dispatch": {"at": at(age)}})
    dispatcher = RecordingDispatcher()

    result = await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=3)

    assert result == DispatchResult(candidates=3, dispatched=3, failed=0, skipped=0)


async def test_no_capacity_runs_no_candidate_query_and_sends_nothing(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("running", lease_seconds=60)
    await make_task("draft", {"dispatch": {"at": at(OLD)}})
    dispatcher = RecordingDispatcher()
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        result = await redispatch_orphans(make_sessionmaker(engine), dispatcher, max_active=1)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert result == DispatchResult(0, 0, 0, 0)
    assert dispatcher.sent == []
    assert len(statements) == 1  # the capacity count only
    assert "LIMIT" not in statements[0].upper()


async def test_starved_orphans_are_a_warning_with_counts(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("running", lease_seconds=60)
    await make_task("draft", {"dispatch": {"at": at(OLD)}})
    await make_task("draft", {"dispatch": {"at": at(OLD * 2)}})

    with capture_logs() as logs:
        await redispatch_orphans(make_sessionmaker(engine), RecordingDispatcher(), max_active=1)

    [event_] = [e for e in logs if e["event"] == "task_dispatch_no_capacity"]
    assert event_["log_level"] == "warning"
    assert (event_["live"], event_["in_flight"], event_["max_active"], event_["orphans"]) == (1, 0, 1, 2)


async def test_no_capacity_without_orphans_is_not_a_warning(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("running", lease_seconds=60)

    with capture_logs() as logs:
        await redispatch_orphans(make_sessionmaker(engine), RecordingDispatcher(), max_active=1)

    assert [e for e in logs if e["log_level"] in ("warning", "error")] == []


async def test_capped_sweep_logs_capacity_and_orphans(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("running", lease_seconds=60)
    for age in (OLD * 3, OLD * 2, OLD):
        await make_task("draft", {"dispatch": {"at": at(age)}})

    with capture_logs() as logs:
        await redispatch_orphans(make_sessionmaker(engine), RecordingDispatcher(), max_active=3)

    [event_] = [e for e in logs if e["event"] == "task_dispatch_capped"]
    assert event_["log_level"] == "info"
    assert (event_["capacity"], event_["orphans"]) == (2, 3)


async def test_uncapped_sweep_logs_no_capacity_event(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("draft", {"dispatch": {"at": at(OLD)}})

    with capture_logs() as logs:
        await redispatch_orphans(make_sessionmaker(engine), RecordingDispatcher(), max_active=MAX_ACTIVE)

    assert [e for e in logs if e["event"] in ("task_dispatch_capped", "task_dispatch_no_capacity")] == []


async def test_max_active_is_required(engine: AsyncEngine) -> None:
    with pytest.raises(TypeError):
        await redispatch_orphans(make_sessionmaker(engine), RecordingDispatcher())  # type: ignore[call-arg]
