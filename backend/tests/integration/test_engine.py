"""TaskEngine (spec WP-3.2 §14 #40, §14.4a): claim, ordered idempotent steps, heartbeat, lease loss.

Fake steps, a real database, a small heartbeat.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.workflow import engine as engine_module
from app.workflow import lease as lease_module
from app.workflow.engine import (
    MAX_PLANNING_ATTEMPTS,
    EngineOutcome,
    Step,
    StepContext,
    StepResult,
    TaskEngine,
    WorkflowPorts,
)
from app.workflow.lease import ClaimResult, LeaseLost
from app.workflow.states import Actor, TaskStatus, transition
from tests.conftest import reset_data
from tests.runtime_fakes import RecordingBus

AUDIT = AuditWriter(Redactor())
WORKER = "w-engine-1"
DISPATCHED = {"at": "2026-11-18T00:00:00+00:00", "count": 2}
PLANNING = ("intake", "context", "plan")


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
        status: str = "draft", checkpoint: dict[str, Any] | None = None, lease_owner: str | None = None
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
            # Test setup: place the task directly (past the writer backstop).
            await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await session.execute(
                text(
                    "UPDATE tasks SET status = :s, lease_owner = CAST(:o AS varchar), lease_expires_at = "
                    "CASE WHEN CAST(:o AS varchar) IS NULL THEN NULL ELSE now() + interval '60 seconds' END "
                    "WHERE id = :id"
                ),
                {"s": status, "o": lease_owner, "id": task.id},
            )
            await session.commit()
            return task.id

    return create


class Steps:
    """Fake steps that record each call; ``script`` maps a step to what it does (default: done)."""

    def __init__(self, **script: Step) -> None:
        self.calls: list[StepContext] = []
        self._script = script

    def registry(self, *names: str) -> dict[str, Step]:
        return {name: self._make(name) for name in names}

    def _make(self, name: str) -> Step:
        async def step(context: StepContext) -> StepResult:
            self.calls.append(context)
            if name in self._script:
                return await self._script[name](context)
            return StepResult.done()

        return step

    @property
    def names(self) -> list[str]:
        return [c.step for c in self.calls]


def make_engine(
    engine: AsyncEngine,
    bus: RecordingBus,
    steps: dict[str, Step],
    *,
    worker: str = WORKER,
    max_active: int = 5,
    heartbeat_s: float = 0.1,
) -> TaskEngine:
    return TaskEngine(
        WorkflowPorts(
            sessions=make_sessionmaker(engine),
            bus=bus,
            audit=AUDIT,
            worker_id=worker,
            max_active=max_active,
            steps=steps,
            heartbeat_s=heartbeat_s,
        )
    )


async def row(engine: AsyncEngine, task_id: uuid.UUID) -> Row[Any]:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT status, status_reason, current_step, checkpoint, lease_owner, lease_expires_at, "
                    "error_code, error_message FROM tasks WHERE id = :id"
                ),
                {"id": task_id},
            )
        ).one()


async def events(engine: AsyncEngine, task_id: uuid.UUID) -> list[tuple[int, str, dict[str, Any]]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT seq, type, payload FROM task_events WHERE task_id = :id ORDER BY seq"),
            {"id": task_id},
        )
        return [(r.seq, r.type, r.payload) for r in rows]


def started(step: str) -> tuple[str, dict[str, Any]]:
    return ("task.step_started", {"step": step})


def finished(step: str) -> tuple[str, dict[str, Any]]:
    return ("task.step_finished", {"step": step, "status": "done"})


def changed(source: str, target: str, reason: str | None) -> tuple[str, dict[str, Any]]:
    return ("task.status_changed", {"from": source, "to": target, "reason": reason})


async def kinds(engine: AsyncEngine, task_id: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    return [(kind, payload) for _, kind, payload in await events(engine, task_id)]


async def to_planned(_: StepContext) -> StepResult:
    return StepResult.transition(TaskStatus.PLANNED, "plan ready")


async def to_interrupted(_: StepContext) -> StepResult:
    return StepResult.transition(TaskStatus.INTERRUPTED, "validation_unavailable")


async def take_over_lease(engine: AsyncEngine, task_id: uuid.UUID) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tasks SET lease_owner = 'w-other', lease_expires_at = now() + interval '60 seconds' "
                "WHERE id = :id"
            ),
            {"id": task_id},
        )


def other_tasks() -> list[asyncio.Task[Any]]:
    return [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]


async def test_runs_planning_steps_in_order_and_records_them(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft", {"user_set": ["title"]})
    steps = Steps(plan=to_planned)
    bus = RecordingBus(engine)

    outcome = await make_engine(engine, bus, steps.registry(*PLANNING)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake", "context", "plan"], "transitioned")
    assert steps.names == ["intake", "context", "plan"]
    # Each step reads the checkpoint as the previous one left it.
    assert [c.checkpoint.get("completed_steps") for c in steps.calls] == [
        None,
        ["intake"],
        ["intake", "context"],
    ]
    assert {c.task_id for c in steps.calls} == {task_id}
    after = await row(engine, task_id)
    assert (after.status, after.status_reason, after.current_step) == ("planned", "plan ready", "plan")
    assert after.checkpoint == {
        "user_set": ["title"],
        "dispatch": {"planning_attempts": 1},  # created although the draft had no dispatch yet
        "completed_steps": ["intake", "context"],
    }
    assert (after.lease_owner, after.lease_expires_at) == (None, None)
    stored = await events(engine, task_id)
    assert [(kind, payload) for _, kind, payload in stored] == [
        started("intake"),
        finished("intake"),
        started("context"),
        finished("context"),
        started("plan"),
        changed("draft", "planned", "plan ready"),
    ]
    assert [seq for seq, _, _ in stored] == [1, 2, 3, 4, 5, 6]
    assert [m["seq"] for m in bus.messages] == [1, 2, 3, 4, 5, 6]
    assert bus.unseen == []  # every event was published after its commit
    async with make_sessionmaker(engine)() as session:
        actors = (
            await session.scalars(
                select(AuditEvent.actor_id).where(
                    AuditEvent.action == "task.status_changed", AuditEvent.resource_id == str(task_id)
                )
            )
        ).all()
    assert actors == ["engine"]


async def test_resume_skips_completed_steps(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task(
        "draft", {"completed_steps": ["intake"], "dispatch": {**DISPATCHED, "planning_attempts": 2}}
    )
    steps = Steps(plan=to_planned)

    outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["context", "plan"], "transitioned")
    assert steps.names == ["context", "plan"]
    after = await row(engine, task_id)
    assert after.status == "planned"
    assert after.checkpoint == {
        "completed_steps": ["intake", "context"],
        "dispatch": {**DISPATCHED, "planning_attempts": 3},  # the third attempt still plans
    }
    assert await kinds(engine, task_id) == [
        started("context"),
        finished("context"),
        started("plan"),
        changed("draft", "planned", "plan ready"),
    ]


async def test_duplicate_delivery_single_execution(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft")
    go = asyncio.Event()

    async def blocked(_: StepContext) -> StepResult:
        await go.wait()
        return StepResult.done()

    steps = Steps(intake=blocked, plan=to_planned)
    registry = steps.registry(*PLANNING)
    first = asyncio.create_task(make_engine(engine, RecordingBus(), registry, worker="w1").run(task_id))
    second = asyncio.create_task(make_engine(engine, RecordingBus(), registry, worker="w2").run(task_id))
    # Whichever claims first runs intake and blocks there; the other delivery must come back meanwhile.
    done, _ = await asyncio.wait({first, second}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
    assert len(done) == 1
    go.set()
    outcomes = await asyncio.gather(first, second)

    assert sorted(o.claim.value for o in outcomes) == ["acquired", "held"]
    held = next(o for o in outcomes if o.claim is ClaimResult.HELD)
    assert held == EngineOutcome(ClaimResult.HELD, [], None)
    assert steps.names == ["intake", "context", "plan"]
    after = await row(engine, task_id)
    assert after.status == "planned"
    assert after.checkpoint["dispatch"] == {"planning_attempts": 1}  # the refused claim is not an attempt


async def test_queued_claim_moves_to_running_atomically(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = await make_task("queued")
    seen: list[tuple[str, str | None]] = []

    async def implement(_: StepContext) -> StepResult:
        current = await row(engine, task_id)
        seen.append((current.status, current.lease_owner))
        return StepResult.transition(TaskStatus.INTERRUPTED, "validation_unavailable")

    steps = Steps(implement=implement)
    bus = RecordingBus(engine)
    outcome = await make_engine(engine, bus, steps.registry("implement")).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["implement"], "transitioned")
    assert seen == [("running", WORKER)]
    after = await row(engine, task_id)
    assert (after.status, after.status_reason, after.lease_owner) == (
        "interrupted",
        "validation_unavailable",
        None,
    )
    assert await kinds(engine, task_id) == [
        changed("queued", "running", None),
        started("implement"),
        changed("running", "interrupted", "validation_unavailable"),
    ]
    assert bus.unseen == []

    # The claim and queued -> running share one transaction: a failed transition leaves no lease behind.
    other = await make_task("queued")

    async def refused(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("transition refused")

    monkeypatch.setattr(engine_module, "transition", refused)
    steps = Steps()
    with pytest.raises(RuntimeError, match="transition refused"):
        await make_engine(engine, RecordingBus(), steps.registry("implement")).run(other)
    after = await row(engine, other)
    assert (after.status, after.lease_owner, after.lease_expires_at) == ("queued", None, None)
    assert steps.names == []


async def test_fourth_planning_claim_fails_the_task(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    checkpoint = {
        "user_set": ["prompt"],
        "completed_steps": ["intake"],
        "dispatch": {**DISPATCHED, "planning_attempts": MAX_PLANNING_ATTEMPTS},
    }
    task_id = await make_task("draft", checkpoint)
    steps = Steps()
    bus = RecordingBus(engine)

    outcome = await make_engine(engine, bus, steps.registry(*PLANNING)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, [], "planning_interrupted")
    assert steps.names == []
    after = await row(engine, task_id)
    assert (after.status, after.error_code) == ("failed", "planning_interrupted")
    assert after.error_message
    assert (after.lease_owner, after.lease_expires_at) == (None, None)
    assert after.checkpoint == {**checkpoint, "dispatch": {**DISPATCHED, "planning_attempts": 4}}
    assert await kinds(engine, task_id) == [changed("draft", "failed", "planning_interrupted")]
    assert bus.unseen == [] and len(bus.messages) == 1


async def test_step_fail_and_transition(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    async def rejects(_: StepContext) -> StepResult:
        return StepResult.fail("intake_rejected", "The request names no repository")

    failing = await make_task("draft")
    steps = Steps(intake=rejects)
    outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(failing)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake"], "failed")
    assert steps.names == ["intake"]
    after = await row(engine, failing)
    assert (after.status, after.error_code, after.error_message, after.lease_owner) == (
        "failed",
        "intake_rejected",
        "The request names no repository",
        None,
    )
    assert "completed_steps" not in after.checkpoint
    assert await kinds(engine, failing) == [started("intake"), changed("draft", "failed", "intake_rejected")]

    async def asks(_: StepContext) -> StepResult:
        return StepResult.transition(TaskStatus.NEEDS_CLARIFICATION, "questions")

    unclear = await make_task("draft")
    steps = Steps(context=asks)
    outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(unclear)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake", "context"], "transitioned")
    assert steps.names == ["intake", "context"]  # the engine stops at the transition
    after = await row(engine, unclear)
    assert (after.status, after.status_reason, after.lease_owner) == (
        "needs_clarification",
        "questions",
        None,
    )
    assert after.checkpoint["completed_steps"] == ["intake"]


async def test_unknown_step_is_unavailable(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft")

    outcome = await make_engine(engine, RecordingBus(), {}).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, [], "step_unavailable")
    after = await row(engine, task_id)
    assert (after.status, after.error_code, after.current_step, after.lease_owner) == (
        "failed",
        "step_unavailable",
        None,
        None,
    )
    assert "intake" in after.error_message
    assert await kinds(engine, task_id) == [changed("draft", "failed", "step_unavailable")]


async def test_external_cancel_between_steps(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft")

    async def cancelled_meanwhile(_: StepContext) -> StepResult:
        async with make_sessionmaker(engine)() as session:
            task = await session.get(Task, task_id)
            assert task is not None
            await transition(
                session, task, TaskStatus.CANCELLED, reason="user", actor=Actor("user", "u1"), audit=AUDIT
            )
            await session.commit()
        return StepResult.done()

    steps = Steps(intake=cancelled_meanwhile)
    outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake"], "status_changed")
    assert steps.names == ["intake"]
    after = await row(engine, task_id)
    assert (after.status, after.lease_owner, after.lease_expires_at) == ("cancelled", None, None)
    assert "completed_steps" not in after.checkpoint  # nothing more written after the cancel
    assert await kinds(engine, task_id) == [started("intake"), changed("draft", "cancelled", "user")]


async def test_heartbeat_extends_lease_during_long_step(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lease_module, "_LEASE", timedelta(seconds=1))
    task_id = await make_task("queued")
    samples: list[tuple[Any, bool, str]] = []

    async def sample() -> None:
        async with engine.connect() as conn:
            got = (
                await conn.execute(
                    text(
                        "SELECT lease_expires_at, lease_expires_at > clock_timestamp() AS live, lease_owner "
                        "FROM tasks WHERE id = :id"
                    ),
                    {"id": task_id},
                )
            ).one()
        samples.append((got.lease_expires_at, got.live, got.lease_owner))

    async def long_step(_: StepContext) -> StepResult:
        await sample()
        await asyncio.sleep(1.5)  # longer than the (shortened) lease
        await sample()
        return StepResult.transition(TaskStatus.INTERRUPTED, "validation_unavailable")

    steps = Steps(implement=long_step)
    outcome = await make_engine(engine, RecordingBus(), steps.registry("implement"), heartbeat_s=0.2).run(
        task_id
    )

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["implement"], "transitioned")
    (first, first_live, _), (last, last_live, owner) = samples
    assert first_live and last_live and owner == WORKER
    assert last > first + timedelta(seconds=1)
    assert (await row(engine, task_id)).status == "interrupted"


@pytest.mark.parametrize("how", ["heartbeat", "after_step", "step_raises"])
async def test_lease_lost_stops_without_writes(engine: AsyncEngine, make_task, how: str) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft")
    cancelled: list[bool] = []

    async def loses_lease(_: StepContext) -> StepResult:
        await take_over_lease(engine, task_id)
        if how == "step_raises":
            raise LeaseLost("the run lost its lease")
        if how == "heartbeat":
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return StepResult.done()

    steps = Steps(intake=loses_lease)
    registry = steps.registry(*PLANNING)
    outcome = await asyncio.wait_for(make_engine(engine, RecordingBus(), registry).run(task_id), timeout=5)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake"], "lease_lost")
    assert steps.names == ["intake"]
    assert cancelled == ([True] if how == "heartbeat" else [])
    after = await row(engine, task_id)
    assert (after.status, after.current_step, after.lease_owner) == ("draft", "intake", "w-other")
    assert "completed_steps" not in after.checkpoint
    assert await kinds(engine, task_id) == [started("intake")]
    assert other_tasks() == []


async def test_at_capacity_returns_without_running(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    await make_task("queued", lease_owner="w-other")
    task_id = await make_task("draft")
    steps = Steps()

    outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING), max_active=1).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.AT_CAPACITY, [], None)
    assert steps.names == []
    after = await row(engine, task_id)
    assert (after.status, after.checkpoint, after.lease_owner) == ("draft", {}, None)
    assert await events(engine, task_id) == []


async def test_no_session_held_during_a_step(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("queued")
    seen: list[tuple[int, int]] = []

    async def implement(_: StepContext) -> StepResult:
        checked_out = engine.pool.checkedout()  # type: ignore[attr-defined]
        async with engine.connect() as conn:
            idle_in_transaction = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                )
            )
        seen.append((checked_out, idle_in_transaction or 0))
        return StepResult.transition(TaskStatus.INTERRUPTED, "validation_unavailable")

    steps = Steps(implement=implement)
    # A long heartbeat interval: no renewal transaction can overlap the check.
    await make_engine(engine, RecordingBus(), steps.registry("implement"), heartbeat_s=30).run(task_id)

    assert seen == [(0, 0)]


@pytest.mark.parametrize("how", ["step_raises", "run_cancelled"])
async def test_step_error_releases_the_lease_and_reraises(engine: AsyncEngine, make_task, how: str) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("queued")
    entered = asyncio.Event()

    async def implement(_: StepContext) -> StepResult:
        entered.set()
        if how == "step_raises":
            raise RuntimeError("step crashed")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    steps = Steps(implement=implement)
    run = asyncio.create_task(make_engine(engine, RecordingBus(), steps.registry("implement")).run(task_id))
    await asyncio.wait_for(entered.wait(), timeout=5)
    if how == "step_raises":
        with pytest.raises(RuntimeError, match="step crashed"):
            await run
    else:
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    after = await row(engine, task_id)
    # No status write and no error code: the recovery sweep owns the task from here.
    assert (after.status, after.error_code, after.lease_owner, after.lease_expires_at) == (
        "running",
        None,
        None,
        None,
    )
    assert await kinds(engine, task_id) == [changed("queued", "running", None), started("implement")]
    assert other_tasks() == []  # the heartbeat stopped


async def test_exhausted_pipeline_releases_and_warns(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft", {"completed_steps": ["intake"]})
    steps = Steps()  # every step says done; none moves the task on

    with capture_logs() as logs:
        outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["context", "plan"], "pipeline_exhausted")
    after = await row(engine, task_id)
    assert (after.status, after.lease_owner) == ("draft", None)
    assert after.checkpoint["completed_steps"] == ["intake", "context", "plan"]
    warnings = [e for e in logs if e["event"] == "pipeline_exhausted"]
    assert [(e["log_level"], e["task_id"]) for e in warnings] == [("warning", str(task_id))]


async def test_completed_steps_never_duplicates(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft")

    async def recorded_elsewhere(_: StepContext) -> StepResult:
        async with engine.begin() as conn:
            await conn.execute(
                text("""UPDATE tasks SET checkpoint = '{"completed_steps": ["intake"]}' WHERE id = :id"""),
                {"id": task_id},
            )
        return StepResult.done()

    steps = Steps(intake=recorded_elsewhere, plan=to_planned)
    await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)

    assert (await row(engine, task_id)).checkpoint["completed_steps"] == ["intake", "context"]
