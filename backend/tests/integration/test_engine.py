"""TaskEngine (spec WP-3.2 §14 #40, §14.4a): claim, ordered idempotent steps, heartbeat, lease loss.

Fake steps, a real database, a small heartbeat.
"""

from __future__ import annotations

import asyncio
import time
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
from app.core.errors import AppError
from app.core.events import emit_task_event
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.worker.identity import delivery_owner
from app.workflow import engine as engine_module
from app.workflow import lease as lease_module
from app.workflow.dispatch import redispatch_orphans
from app.workflow.engine import (
    MAX_PLANNING_ATTEMPTS,
    EngineOutcome,
    Step,
    StepAbandoned,
    StepContext,
    StepResult,
    TaskEngine,
    WorkflowPorts,
)
from app.workflow.lease import (
    LEASE_SAFETY_SECONDS,
    LEASE_SECONDS,
    ClaimResult,
    LeaseGuard,
    LeaseLost,
    claim_lease,
)
from app.workflow.recovery import recover_expired
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
        status: str = "draft",
        checkpoint: dict[str, Any] | None = None,
        lease_owner: str | None = None,
        *,
        lease_seconds: float = 60,  # negative: a lease that already expired
        current_step: str | None = None,
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
                    "UPDATE tasks SET status = :s, current_step = CAST(:c AS varchar), "
                    "lease_owner = CAST(:o AS varchar), lease_expires_at = CASE "
                    "WHEN CAST(:o AS varchar) IS NULL THEN NULL "
                    "ELSE now() + make_interval(secs => CAST(:l AS double precision)) END "
                    "WHERE id = :id"
                ),
                {"s": status, "c": current_step, "o": lease_owner, "l": lease_seconds, "id": task.id},
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


class BreakableSessions:
    """The engine's sessionmaker until ``mode`` breaks it: ``fails`` raises on open, ``hangs`` never opens."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._real = make_sessionmaker(engine)
        self.mode: str | None = None

    def __call__(self) -> Any:
        if self.mode == "fails":
            raise OSError("database unreachable")
        if self.mode == "hangs":
            return Hung()
        return self._real()


class Hung:
    """A hung connection; bounded, so an engine without a renewal timeout fails the test, not hangs it."""

    async def __aenter__(self) -> None:
        await asyncio.sleep(3)
        raise OSError("connection hung")

    async def __aexit__(self, *exc: object) -> None:
        return None


def make_engine(
    engine: AsyncEngine,
    bus: RecordingBus,
    steps: dict[str, Step],
    *,
    worker: str = WORKER,
    max_active: int = 5,
    heartbeat_s: float = 0.1,
    lease_safety_s: float = LEASE_SAFETY_SECONDS,
    sessions: Any = None,
) -> TaskEngine:
    return TaskEngine(
        WorkflowPorts(
            sessions=sessions or make_sessionmaker(engine),
            bus=bus,
            audit=AUDIT,
            worker_id=worker,
            max_active=max_active,
            steps=steps,
            heartbeat_s=heartbeat_s,
            lease_safety_s=lease_safety_s,
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


def finished(step: str, status: str = "done") -> tuple[str, dict[str, Any]]:
    return ("task.step_finished", {"step": step, "status": status})


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
        "completed_steps": ["intake", "context", "plan"],  # plan decided the next status: it is done
    }
    assert (after.lease_owner, after.lease_expires_at) == (None, None)
    stored = await events(engine, task_id)
    assert [(kind, payload) for _, kind, payload in stored] == [
        started("intake"),
        finished("intake"),
        started("context"),
        finished("context"),
        started("plan"),
        finished("plan"),  # same transaction as the status change, just before it
        changed("draft", "planned", "plan ready"),
    ]
    assert [seq for seq, _, _ in stored] == [1, 2, 3, 4, 5, 6, 7]
    assert [m["seq"] for m in bus.messages] == [1, 2, 3, 4, 5, 6, 7]
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
        "completed_steps": ["intake", "context", "plan"],
        "dispatch": {**DISPATCHED, "planning_attempts": 3},  # the third attempt still plans
    }
    assert await kinds(engine, task_id) == [
        started("context"),
        finished("context"),
        started("plan"),
        finished("plan"),
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
        finished("implement"),
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
    assert "completed_steps" not in after.checkpoint  # a retry runs intake again
    assert await kinds(engine, failing) == [
        started("intake"),
        finished("intake", "failed"),
        changed("draft", "failed", "intake_rejected"),
    ]

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
    assert after.checkpoint["completed_steps"] == ["intake", "context"]
    assert (await kinds(engine, unclear))[-2:] == [
        finished("context"),
        changed("draft", "needs_clarification", "questions"),
    ]


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


async def cancel_as_user(engine: AsyncEngine, task_id: uuid.UUID) -> None:
    async with make_sessionmaker(engine)() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        await transition(
            session, task, TaskStatus.CANCELLED, reason="user", actor=Actor("user", "u1"), audit=AUDIT
        )
        await session.commit()


async def test_external_cancel_during_a_step(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft")

    async def cancelled_meanwhile(_: StepContext) -> StepResult:
        await cancel_as_user(engine, task_id)
        return StepResult.done()

    steps = Steps(intake=cancelled_meanwhile)
    outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake"], "status_changed")
    assert steps.names == ["intake"]
    after = await row(engine, task_id)
    assert (after.status, after.lease_owner, after.lease_expires_at) == ("cancelled", None, None)
    assert "completed_steps" not in after.checkpoint  # nothing more written after the cancel
    assert await kinds(engine, task_id) == [started("intake"), changed("draft", "cancelled", "user")]


async def test_external_cancel_between_steps(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intake is finished and committed; the cancel lands before context's start re-reads the status."""
    task_id = await make_task("draft")
    finish = TaskEngine._finish

    async def finish_then_cancel(self: TaskEngine, *args: Any) -> Any:
        stopped = await finish(self, *args)
        await cancel_as_user(engine, task_id)
        return stopped

    monkeypatch.setattr(TaskEngine, "_finish", finish_then_cancel)
    steps = Steps()
    outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake"], "status_changed")
    assert steps.names == ["intake"]  # context never runs
    after = await row(engine, task_id)
    assert (after.status, after.current_step, after.lease_owner, after.lease_expires_at) == (
        "cancelled",
        "intake",
        None,
        None,
    )
    assert after.checkpoint["completed_steps"] == ["intake"]
    assert await kinds(engine, task_id) == [
        started("intake"),
        finished("intake"),
        changed("draft", "cancelled", "user"),  # the user's; the engine writes no transition
    ]


async def test_heartbeat_extends_lease_during_long_step(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lease_module, "_LEASE", timedelta(seconds=2))
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
        await asyncio.sleep(2.5)  # longer than the (shortened) lease
        await sample()
        return StepResult.transition(TaskStatus.INTERRUPTED, "validation_unavailable")

    steps = Steps(implement=long_step)
    outcome = await make_engine(
        engine, RecordingBus(), steps.registry("implement"), heartbeat_s=0.2, lease_safety_s=0.5
    ).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["implement"], "transitioned")
    (first, first_live, _), (last, last_live, owner) = samples
    assert first_live and last_live and owner == WORKER
    assert last > first + timedelta(seconds=1)
    assert (await row(engine, task_id)).status == "interrupted"


@pytest.mark.parametrize("how", ["heartbeat", "after_step"])
async def test_a_second_delivery_in_the_same_process_fences_the_first(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    how: str,
) -> None:
    """L6: two deliveries of one worker process hold different owners, so once B claims after A's lease
    expired, A's heartbeat or finish raises LeaseLost and never extends B's lease."""
    owner_a, owner_b = delivery_owner(), delivery_owner()
    assert owner_a != owner_b
    task_id = await make_task("draft")
    b_expiry: list[Any] = []

    async def expires_then_b_claims(_: StepContext) -> StepResult:
        # Expiry and B's claim commit together: a heartbeat of A in between would see a dead lease, stop the
        # step before B claims, and fail the test for the wrong reason (review T2).
        async with make_sessionmaker(engine)() as session:
            await session.execute(
                text("UPDATE tasks SET lease_expires_at = now() - interval '1 second' WHERE id = :id"),
                {"id": task_id},
            )
            claimed = await claim_lease(
                session, task_id, owner_b, max_active=5, claimable=frozenset({"draft"})
            )
            await session.commit()
        assert claimed is ClaimResult.ACQUIRED
        b_expiry.append((await row(engine, task_id)).lease_expires_at)
        if how == "heartbeat":
            await asyncio.sleep(10)
        return StepResult.done()

    steps = Steps(intake=expires_then_b_claims)
    beat = 30 if how == "after_step" else 0.1
    engine_a = make_engine(
        engine, RecordingBus(), steps.registry(*PLANNING), worker=owner_a, heartbeat_s=beat
    )
    outcome = await asyncio.wait_for(engine_a.run(task_id), timeout=5)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake"], "lease_lost")
    after = await row(engine, task_id)
    assert after.lease_owner == owner_b
    assert after.lease_expires_at == b_expiry[0]


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
    # after_step: no beat may come first, so the loss is found by the guard before the finish write.
    beat = 30 if how == "after_step" else 0.1
    task_engine = make_engine(engine, RecordingBus(), steps.registry(*PLANNING), heartbeat_s=beat)
    outcome = await asyncio.wait_for(task_engine.run(task_id), timeout=5)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake"], "lease_lost")
    assert steps.names == ["intake"]
    assert cancelled == ([True] if how == "heartbeat" else [])
    after = await row(engine, task_id)
    assert (after.status, after.current_step, after.lease_owner) == ("draft", "intake", "w-other")
    assert "completed_steps" not in after.checkpoint
    assert await kinds(engine, task_id) == [started("intake")]
    assert other_tasks() == []


@pytest.mark.parametrize("how", ["fails", "hangs"])
async def test_heartbeat_gives_up_before_the_lease_runs_out(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    """R-T3h: renewals that keep failing (or hang) end the step as a lost lease, not retried forever."""
    monkeypatch.setattr(lease_module, "_LEASE", timedelta(seconds=1))
    task_id = await make_task("draft")
    sessions = BreakableSessions(engine)
    cancelled: list[bool] = []

    async def long_step(_: StepContext) -> StepResult:
        sessions.mode = how  # from now on every renewal fails or hangs
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return StepResult.done()

    steps = Steps(intake=long_step)
    task_engine = make_engine(
        engine, RecordingBus(), steps.registry(*PLANNING), lease_safety_s=0.3, sessions=sessions
    )
    with capture_logs() as logs:
        outcome = await asyncio.wait_for(task_engine.run(task_id), timeout=5)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["intake"], "lease_lost")
    assert cancelled == [True]
    failed = [e for e in logs if e["event"] == "lease_heartbeat_failed"]
    assert failed and {e["error_type"] for e in failed} == {"OSError" if how == "fails" else "TimeoutError"}
    after = await row(engine, task_id)
    assert (after.status, after.current_step, after.lease_owner) == ("draft", "intake", WORKER)
    assert "completed_steps" not in after.checkpoint  # nothing more written
    assert await kinds(engine, task_id) == [started("intake")]
    assert other_tasks() == []


async def test_step_error_dropped_for_lease_loss_is_logged(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("draft")

    async def fails_on_cancel(_: StepContext) -> StepResult:
        await take_over_lease(engine, task_id)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise RuntimeError("cleanup failed for token sk-test-secret-0123") from None
        return StepResult.done()

    steps = Steps(intake=fails_on_cancel)
    with capture_logs() as logs:
        outcome = await asyncio.wait_for(
            make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id), timeout=5
        )

    assert outcome.stopped == "lease_lost"
    dropped = [e for e in logs if e["event"] == "step_error_dropped"]
    assert [(e["log_level"], e["task_id"], e["step"], e["error_type"]) for e in dropped] == [
        ("error", str(task_id), "intake", "RuntimeError")
    ]
    assert "sk-test-secret" not in repr(logs)  # the type only, never the text


async def test_resume_after_a_dead_worker(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    """A worker died during context: its lease expired; intake is recorded and its events stay."""
    task_id = await make_task(
        "draft",
        {"completed_steps": ["intake"], "dispatch": {**DISPATCHED, "planning_attempts": 1}},
        lease_owner="w-dead",
        lease_seconds=-5,
        current_step="context",
    )
    async with make_sessionmaker(engine)() as session:
        for kind, payload in (started("intake"), finished("intake"), started("context")):
            await emit_task_event(session, None, task_id, kind, payload, redactor=AUDIT.redactor)
        await session.commit()
    steps = Steps(plan=to_planned)

    outcome = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["context", "plan"], "transitioned")
    assert steps.names == ["context", "plan"]
    after = await row(engine, task_id)
    assert (after.status, after.lease_owner) == ("planned", None)
    assert after.checkpoint["dispatch"] == {**DISPATCHED, "planning_attempts": 2}
    assert after.checkpoint["completed_steps"] == ["intake", "context", "plan"]
    stored = await events(engine, task_id)
    assert [seq for seq, _, _ in stored] == [1, 2, 3, 4, 5, 6, 7, 8]  # numbering goes on without a gap
    assert [(kind, payload) for _, kind, payload in stored][3:] == [
        started("context"),
        finished("context"),
        started("plan"),
        finished("plan"),
        changed("draft", "planned", "plan ready"),
    ]


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


async def test_unexpected_step_error_is_logged_with_its_type_only(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    """Spec #43: an unexpected error is logged with the task id, then re-raised (type only, no text)."""
    task_id = await make_task("queued")
    secret = "sk-test-" + "secret-0123"

    async def implement(_: StepContext) -> StepResult:
        raise RuntimeError("step crashed with token " + secret)

    steps = Steps(implement=implement)
    with capture_logs() as logs, pytest.raises(RuntimeError):
        await make_engine(engine, RecordingBus(), steps.registry("implement")).run(task_id)

    failed = [e for e in logs if e["event"] == "task_engine_failed"]
    assert [(e["log_level"], e["task_id"], e["worker_id"], e["step"], e["error_type"]) for e in failed] == [
        ("error", str(task_id), WORKER, "implement", "RuntimeError")
    ]
    assert secret not in repr(logs)
    assert all("exc_info" not in e for e in logs)


async def test_cancelled_run_is_logged_as_cancelled(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("queued")
    entered = asyncio.Event()

    async def implement(_: StepContext) -> StepResult:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    steps = Steps(implement=implement)
    with capture_logs() as logs:
        run = asyncio.create_task(
            make_engine(engine, RecordingBus(), steps.registry("implement")).run(task_id)
        )
        await asyncio.wait_for(entered.wait(), timeout=5)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    assert [e["event"] for e in logs if e["event"].startswith("task_engine_")] == ["task_engine_cancelled"]
    cancelled = next(e for e in logs if e["event"] == "task_engine_cancelled")
    assert (cancelled["log_level"], cancelled["task_id"], cancelled["step"]) == (
        "warning",
        str(task_id),
        "implement",
    )


async def test_release_failure_after_an_error_logs_the_type_only(
    engine: AsyncEngine,
    make_task,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    task_id = await make_task("queued")
    secret = "sk-test-" + "release-0123"

    async def broken_release(*_: Any, **__: Any) -> None:
        raise OSError("cannot release " + secret)

    async def implement(_: StepContext) -> StepResult:
        monkeypatch.setattr(engine_module, "release_lease", broken_release)
        raise RuntimeError("step crashed")

    steps = Steps(implement=implement)
    with capture_logs() as logs, pytest.raises(RuntimeError, match="step crashed"):
        await make_engine(engine, RecordingBus(), steps.registry("implement")).run(task_id)

    release = [e for e in logs if e["event"] == "lease_release_failed"]
    assert [(e["task_id"], e["error_type"]) for e in release] == [(str(task_id), "OSError")]
    assert "exc_info" not in release[0]
    assert secret not in repr(logs)


async def test_heartbeat_give_up_is_logged(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renewal hung from the first beat gives up at the deadline with no retry line before it: the give-up
    itself must say why, or a database outage looks like a takeover."""
    monkeypatch.setattr(lease_module, "_LEASE", timedelta(seconds=1))
    task_id = await make_task("draft")
    sessions = BreakableSessions(engine)

    async def long_step(_: StepContext) -> StepResult:
        sessions.mode = "hangs"
        await asyncio.sleep(10)
        return StepResult.done()

    steps = Steps(intake=long_step)
    # H = last_ok + 0.8: the first attempt starts at ~0.5 and is bound by H itself, so it gives up at once and
    # no lease_heartbeat_failed line comes first.
    task_engine = make_engine(
        engine,
        RecordingBus(),
        steps.registry(*PLANNING),
        heartbeat_s=0.5,
        lease_safety_s=0.2,
        sessions=sessions,
    )
    with capture_logs() as logs:
        outcome = await asyncio.wait_for(task_engine.run(task_id), timeout=5)

    assert outcome.stopped == "lease_lost"
    assert [e for e in logs if e["event"] == "lease_heartbeat_failed"] == []
    gave_up = [e for e in logs if e["event"] == "lease_heartbeat_gave_up"]
    assert [(e["log_level"], e["task_id"], e["error_type"]) for e in gave_up] == [
        ("warning", str(task_id), "TimeoutError")
    ]


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

    assert (await row(engine, task_id)).checkpoint["completed_steps"] == ["intake", "context", "plan"]


def pipeline_of(status: str) -> tuple[str, tuple[str, ...]]:
    """The status a task is created in, and the steps to register, for a delivery of ``status``'s pipeline."""
    return ("draft", PLANNING) if status == "draft" else ("queued", ("implement",))


async def requeue_directly(engine: AsyncEngine, task_id: uuid.UUID) -> None:
    """Test setup: interrupted -> queued is 3.2f's edge, so the task is placed back as make_task places it."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
        await conn.execute(text("UPDATE tasks SET status = 'queued' WHERE id = :id"), {"id": task_id})


async def abandons(_: StepContext) -> StepResult:
    raise StepAbandoned("provider_not_ready")


class SentTasks:
    """A dispatcher that records what the sweep hands over."""

    def __init__(self) -> None:
        self.sent: list[uuid.UUID] = []

    async def enqueue_task(self, task_id: uuid.UUID) -> None:
        self.sent.append(task_id)


async def test_interrupt_leaves_the_step_undone_and_a_resume_runs_it_again(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
) -> None:
    """Spec §16.5: interrupt moves running -> interrupted without recording the step, so a resume runs it."""
    task_id = await make_task("queued", {"user_set": ["title"]})

    async def interrupts(_: StepContext) -> StepResult:
        return StepResult.interrupt("validation_unavailable")

    steps = Steps(implement=interrupts)
    bus = RecordingBus(engine)
    outcome = await make_engine(engine, bus, steps.registry("implement")).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["implement"], "interrupted")
    after = await row(engine, task_id)
    assert (
        after.status,
        after.status_reason,
        after.error_code,
        after.lease_owner,
        after.lease_expires_at,
    ) == (
        "interrupted",
        "validation_unavailable",
        None,
        None,
        None,
    )
    assert after.checkpoint == {"user_set": ["title"]}  # no completed_steps: the step's work is not done
    assert await kinds(engine, task_id) == [
        changed("queued", "running", None),
        started("implement"),
        finished("implement", "interrupted"),  # same savepoint as the status change, just before it
        changed("running", "interrupted", "validation_unavailable"),
    ]
    assert bus.unseen == []

    await requeue_directly(engine, task_id)
    outcome = await make_engine(engine, RecordingBus(), steps.registry("implement")).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, ["implement"], "interrupted")
    assert steps.names == ["implement", "implement"]  # the resumed delivery ran the step again


@pytest.mark.parametrize(
    ("status", "kind", "target"),
    [
        ("draft", "interrupt", "interrupted"),  # interrupt is only for running
        ("draft", "transition", "completed"),  # refused by the state machine too: never raised (#75)
        ("draft", "transition", "failed"),  # the state machine allows it; failed only through fail()
        ("running", "transition", "cancelled"),  # the state machine allows it; never a step's choice
    ],
)
async def test_target_outside_the_pipeline_allowlist_fails_the_task(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    status: str,
    kind: str,
    target: str,
) -> None:
    """Spec §16.4 #75: a refused target is written honestly and terminally, with no raise that would spend a
    planning attempt, and the step joins no completed_steps."""
    created, registered = pipeline_of(status)
    task_id = await make_task(created)
    step = registered[0]

    async def returns(_: StepContext) -> StepResult:
        if kind == "interrupt":
            return StepResult.interrupt("validation_unavailable")
        return StepResult.transition(TaskStatus(target), "skip")

    steps = Steps(**{step: returns})
    bus = RecordingBus(engine)
    with capture_logs() as logs:
        outcome = await make_engine(engine, bus, steps.registry(*registered)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, [step], "step_result_invalid")
    assert steps.names == [step]  # the engine stops at the refused result
    after = await row(engine, task_id)
    assert (
        after.status,
        after.status_reason,
        after.error_code,
        after.lease_owner,
        after.lease_expires_at,
    ) == (
        "failed",
        "step_result_invalid",
        "step_result_invalid",
        None,
        None,
    )
    assert step in after.error_message and target in after.error_message
    assert "completed_steps" not in after.checkpoint
    assert (await kinds(engine, task_id))[-3:] == [
        started(step),
        finished(step, "failed"),
        changed(status, "failed", "step_result_invalid"),
    ]
    assert bus.unseen == []
    invalid = [e for e in logs if e["event"] == "step_result_invalid"]
    assert [(e["log_level"], e["task_id"], e["step"], e["kind"], e["target"]) for e in invalid] == [
        ("error", str(task_id), step, kind, target)
    ]
    assert [e for e in logs if e["event"] == "task_engine_failed"] == []


@pytest.mark.parametrize("how", ["transition", "fail", "step_result_invalid"])
async def test_refused_status_write_undoes_the_step_finish(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    """Ruling R-T3f: the step's finish and its status write share one savepoint, so a refused write also
    undoes the step_finished event and the completed_steps entry."""
    task_id = await make_task("draft")

    async def refused(*_: Any, **__: Any) -> None:
        raise AppError("VERSION_CONFLICT", "The task changed meanwhile", http_status=409)

    # A draft claim calls neither: it moves no status and only counts its attempt.
    monkeypatch.setattr(engine_module, "transition" if how == "transition" else "fail_task", refused)

    async def result(_: StepContext) -> StepResult:
        if how == "transition":
            return StepResult.transition(TaskStatus.PLANNED, "plan ready")  # allowed: done, then refused
        if how == "fail":
            return StepResult.fail("intake_rejected", "The request names no repository")
        return StepResult.transition(TaskStatus.COMPLETED, "skip")

    steps = Steps(intake=result)
    bus = RecordingBus(engine)
    with pytest.raises(AppError) as caught:
        await make_engine(engine, bus, steps.registry(*PLANNING)).run(task_id)

    assert caught.value.code == "VERSION_CONFLICT"
    assert steps.names == ["intake"]
    after = await row(engine, task_id)
    # R-T3c: the error goes up, the lease is released, no status or error code is written.
    assert (after.status, after.error_code, after.current_step, after.lease_owner) == (
        "draft",
        None,
        "intake",
        None,
    )
    assert "completed_steps" not in after.checkpoint
    assert await kinds(engine, task_id) == [started("intake")]
    assert [m["type"] for m in bus.messages] == ["task.step_started"]  # no step_finished was published


@pytest.mark.parametrize("how", ["fail", "interrupt"])
async def test_refused_result_leaves_no_step_finish(engine: AsyncEngine, make_task, how: str) -> None:  # type: ignore[no-untyped-def]
    """Spec §16.5a #6: a code off the tasks.error_code pattern is refused when the step makes its result: a
    bug of the step (R-T3c), so no status, event or checkpoint is written for it."""
    task_id = await make_task("draft")

    async def refused(_: StepContext) -> StepResult:
        if how == "fail":
            return StepResult.fail("", "an empty error code is refused")
        return StepResult.interrupt("Validation unavailable")

    steps = Steps(intake=refused)
    bus = RecordingBus(engine)
    with pytest.raises(ValueError, match="error_code" if how == "fail" else "reason"):
        await make_engine(engine, bus, steps.registry(*PLANNING)).run(task_id)

    assert steps.names == ["intake"]  # the engine stops: nothing after the refused step runs
    after = await row(engine, task_id)
    # R-T3c: the error goes up, the lease is released, no status or error code is written.
    assert (after.status, after.error_code, after.current_step, after.lease_owner) == (
        "draft",
        None,
        "intake",
        None,
    )
    assert "completed_steps" not in after.checkpoint
    assert await kinds(engine, task_id) == [started("intake")]
    assert [m["type"] for m in bus.messages] == ["task.step_started"]  # no step_finished was published


@pytest.mark.parametrize("status", ["draft", "running"])
async def test_step_abandoned_writes_nothing_and_releases_the_lease(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    status: str,
) -> None:
    """Spec §16.5: a step that gives up raises StepAbandoned; the delivery returns (no raise to Celery), the
    lease is released, and no status, event or checkpoint is written for the step."""
    created, registered = pipeline_of(status)
    task_id = await make_task(created)
    step = registered[0]
    steps = Steps(**{step: abandons})
    bus = RecordingBus(engine)

    with capture_logs() as logs:
        outcome = await make_engine(engine, bus, steps.registry(*registered)).run(task_id)

    assert outcome == EngineOutcome(ClaimResult.ACQUIRED, [step], "abandoned")
    after = await row(engine, task_id)
    assert (
        after.status,
        after.error_code,
        after.current_step,
        after.lease_owner,
        after.lease_expires_at,
    ) == (
        status,
        None,
        step,
        None,
        None,
    )
    # Only the claim's own writes stay: a draft's planning attempt, a queued task's move to running.
    assert after.checkpoint == ({"dispatch": {"planning_attempts": 1}} if status == "draft" else {})
    claimed = [] if status == "draft" else [changed("queued", "running", None)]
    assert await kinds(engine, task_id) == [*claimed, started(step)]
    assert bus.unseen == []
    abandoned = [e for e in logs if e["event"] == "step_abandoned"]
    assert [(e["log_level"], e["task_id"], e["step"], e["reason"]) for e in abandoned] == [
        ("warning", str(task_id), step, "provider_not_ready")
    ]
    assert [e for e in logs if e["event"] in ("task_engine_failed", "step_error_dropped")] == []
    assert all("exc_info" not in e for e in logs)
    assert other_tasks() == []  # the heartbeat stopped


async def test_abandoned_draft_is_sent_again_and_counts_an_attempt(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    """Spec §16.5: with its lease released, the dispatch sweep sends the draft again, and that delivery's
    claim counts another planning attempt (a step that keeps giving up ends at planning_interrupted)."""
    task_id = await make_task("draft", {"dispatch": DISPATCHED})
    first = await make_engine(engine, RecordingBus(), Steps(intake=abandons).registry(*PLANNING)).run(task_id)
    assert first.stopped == "abandoned"

    dispatcher = SentTasks()
    swept = await redispatch_orphans(make_sessionmaker(engine), dispatcher)

    assert (swept.dispatched, dispatcher.sent) == (1, [task_id])
    steps = Steps(plan=to_planned)
    second = await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)
    assert second == EngineOutcome(ClaimResult.ACQUIRED, list(PLANNING), "transitioned")  # intake runs again
    dispatch = (await row(engine, task_id)).checkpoint["dispatch"]
    assert (dispatch["count"], dispatch["planning_attempts"]) == (DISPATCHED["count"] + 1, 2)


async def test_abandoned_running_task_is_left_to_recovery(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    """N49 (spec §16.4 #79): a running task whose step gave up holds no lease; once the no-lease grace has
    passed the recovery sweep moves it to interrupted lease_expired."""
    task_id = await make_task("queued")
    outcome = await make_engine(engine, RecordingBus(), Steps(implement=abandons).registry("implement")).run(
        task_id
    )
    assert outcome.stopped == "abandoned"
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tasks SET updated_at = now() - make_interval(secs => :s) WHERE id = :id"),
            {"s": LEASE_SECONDS * 2, "id": task_id},
        )

    swept = await recover_expired(make_sessionmaker(engine), RecordingBus(), AUDIT)

    assert swept.recovered == 1
    after = await row(engine, task_id)
    assert (after.status, after.status_reason, after.lease_owner) == ("interrupted", "lease_expired", None)


# --- Task 3 (spec §16.4 #76): every engine transaction is bounded ---


class SpySessions:
    """The real sessionmaker; records the first statement each session executes."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._real = make_sessionmaker(engine)
        self.first: list[str] = []

    def __call__(self) -> Any:
        session = self._real()
        recorded = False

        def wrap(name: str) -> None:
            original = getattr(session, name)

            async def spy(statement: Any, *args: Any, **kwargs: Any) -> Any:
                nonlocal recorded
                if not recorded:
                    recorded = True
                    compiled = statement.compile()
                    self.first.append(f"{compiled} {sorted(map(str, compiled.params.values()))}")
                return await original(statement, *args, **kwargs)

            setattr(session, name, spy)

        for name in ("execute", "scalar", "scalars"):
            wrap(name)
        return session


@pytest.mark.parametrize("scenario", ["planned", "pipeline_end", "step_error"])
async def test_every_engine_session_starts_with_bound_transaction(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    scenario: str,
) -> None:
    task_id = await make_task(
        "draft", {"completed_steps": ["intake"]} if scenario == "pipeline_end" else None
    )

    async def slow(_: StepContext) -> StepResult:
        await asyncio.sleep(0.35)  # several heartbeats of 0.1 s
        if scenario == "step_error":
            raise RuntimeError("step crashed")
        return StepResult.done()

    steps = Steps(intake=slow, plan=to_planned) if scenario == "planned" else Steps(context=slow)
    spy = SpySessions(engine)
    task_engine = make_engine(engine, RecordingBus(), steps.registry(*PLANNING), sessions=spy)
    if scenario == "step_error":
        with pytest.raises(RuntimeError, match="step crashed"):
            await task_engine.run(task_id)
    else:
        outcome = await task_engine.run(task_id)
        if scenario == "pipeline_end":
            assert outcome.stopped == "pipeline_exhausted"  # the end-of-_drive release session ran
    if scenario != "planned":
        # pipeline_end: the end-of-_drive release; step_error: _release_after_error. Both sessions ran.
        assert ((await row(engine, task_id)).lease_owner) is None

    # claim, start/finish pairs, heartbeats and the release: at least 1 + 2 * steps sessions each way.
    assert len(spy.first) >= 5
    for first in spy.first:
        assert first.startswith("SELECT set_config(") and first.count("set_config(") == 2, first
        assert "'lock_timeout'" in first and "'statement_timeout'" in first, first
    if scenario == "planned":
        assert (await row(engine, task_id)).status == "planned"


async def test_lock_timeout_on_the_claim_is_busy(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.core.db as db_module

    monkeypatch.setattr(db_module, "LOCK_TIMEOUT", "200ms")
    task_id = await make_task("queued", lease_owner="dead", lease_seconds=-5)
    before = await row(engine, task_id)
    steps = Steps()
    async with engine.connect() as holder:
        await holder.execute(text("SELECT id FROM tasks WHERE id = :id FOR UPDATE"), {"id": task_id})
        started_at = time.monotonic()
        outcome = await asyncio.wait_for(
            make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id), timeout=10
        )
        elapsed = time.monotonic() - started_at
        await holder.rollback()

    assert outcome == EngineOutcome(ClaimResult.BUSY, [], None)
    assert elapsed < 3
    assert steps.names == []
    assert await row(engine, task_id) == before


class StalledSession:
    """A session on a black-holed connection: every statement waits on something that never happens."""

    async def __aenter__(self) -> StalledSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def close(self) -> None:
        return None

    async def execute(self, *_: Any, **__: Any) -> Any:
        await asyncio.Event().wait()

    scalar = scalars = rollback = commit = execute


class StallingSessions:
    """The real sessionmaker until armed; then every new session stalls."""

    def __init__(self, engine: AsyncEngine, *, armed: bool) -> None:
        self._real = make_sessionmaker(engine)
        self.armed = armed

    def __call__(self) -> Any:
        return StalledSession() if self.armed else self._real()


@pytest.mark.parametrize("stall_at", ["claim", "mid_run"])
async def test_a_black_holed_database_ends_the_run_within_the_bound(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
    stall_at: str,
) -> None:
    monkeypatch.setattr(engine_module, "ENGINE_WRITE_TIMEOUT_S", 1)
    task_id = await make_task("draft")
    sessions = StallingSessions(engine, armed=stall_at == "claim")

    async def intake(_: StepContext) -> StepResult:
        sessions.armed = True  # the connection goes dark while the step runs
        await asyncio.sleep(0.2)
        return StepResult.done()

    steps = Steps(intake=intake)
    task_engine = make_engine(engine, RecordingBus(), steps.registry(*PLANNING), sessions=sessions)
    started_at = time.monotonic()
    with capture_logs() as logs, pytest.raises(TimeoutError):
        await asyncio.wait_for(task_engine.run(task_id), timeout=10)
    elapsed = time.monotonic() - started_at

    assert elapsed <= 2 * (1 + 2.5)
    failed = [e for e in logs if e["event"] == "task_engine_failed"]
    if stall_at == "mid_run":
        assert [(e["task_id"], e["error_type"]) for e in failed] == [(str(task_id), "TimeoutError")]
        assert [e["error_type"] for e in logs if e["event"] == "lease_release_failed"] == ["TimeoutError"]
    else:
        assert failed == []  # _claim sits outside run()'s try: the timeout simply propagates


@pytest.mark.parametrize(
    ("beat", "safety"),
    [(0, 10), (-1, 10), (20, 0), (20, -1), (50, 10), (60, 1), (40, 30)],
    ids=[
        "beat_zero",
        "beat_negative",
        "safety_zero",
        "safety_negative",
        "sum_equals_lease",
        "beat_over_lease",
        "sum_over_lease",
    ],
)
async def test_engine_rejects_unusable_heartbeat_settings(
    engine: AsyncEngine, beat: float, safety: float
) -> None:
    """Spec §16.5a #5: heartbeat_s > 0, lease_safety_s > 0 and heartbeat_s + lease_safety_s < lease."""
    with pytest.raises(ValueError, match="heartbeat_s") as raised:
        make_engine(engine, RecordingBus(), {}, heartbeat_s=beat, lease_safety_s=safety)
    assert str(raised.value) == "heartbeat_s and lease_safety_s must be > 0 and sum to less than the lease"


async def test_engine_accepts_the_production_heartbeat_settings(engine: AsyncEngine) -> None:
    make_engine(engine, RecordingBus(), {}, heartbeat_s=20, lease_safety_s=10)
    assert LEASE_SAFETY_SECONDS == 10


async def claimed(engine: AsyncEngine, make_task) -> uuid.UUID:  # type: ignore[no-untyped-def]
    task_id = await make_task("queued")
    async with make_sessionmaker(engine)() as db:
        assert await claim_lease(db, task_id, WORKER, max_active=5) is ClaimResult.ACQUIRED
        await db.commit()
    return task_id


async def test_lease_guard_hold_extends_the_lease_in_the_callers_transaction(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
) -> None:
    task_id = await claimed(engine, make_task)
    before = (await row(engine, task_id)).lease_expires_at
    async with make_sessionmaker(engine)() as db:
        await LeaseGuard(task_id, WORKER).hold(db)
        assert (await row(engine, task_id)).lease_expires_at == before  # not committed yet
        await db.commit()
    assert (await row(engine, task_id)).lease_expires_at > before


@pytest.mark.parametrize("how", ["wrong_owner", "expired"])
async def test_lease_guard_hold_raises_lease_lost(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    how: str,
) -> None:
    task_id = await claimed(engine, make_task)
    if how == "expired":
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE tasks SET lease_expires_at = now() - interval '1 second' WHERE id = :id"),
                {"id": task_id},
            )
    guard = LeaseGuard(task_id, "w-other" if how == "wrong_owner" else WORKER)
    async with make_sessionmaker(engine)() as db:
        with pytest.raises(LeaseLost):
            await guard.hold(db)


async def test_lease_guard_hides_the_owner_and_is_frozen() -> None:
    guard = LeaseGuard(uuid.uuid4(), "w-secret-owner")
    assert "w-secret-owner" not in repr(guard)
    with pytest.raises(AttributeError):
        guard.task_id = uuid.uuid4()  # type: ignore[misc]


async def test_a_step_receives_a_lease_guard_it_can_hold_in_its_own_session(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
) -> None:
    task_id = await make_task("draft")
    seen: list[Any] = []

    async def holds(context: StepContext) -> StepResult:
        before = (await row(engine, task_id)).lease_expires_at
        async with make_sessionmaker(engine)() as db:
            await context.lease.hold(db)
            await db.commit()
        seen.append((context.lease, (await row(engine, task_id)).lease_expires_at > before))
        return StepResult.done()

    steps = Steps(intake=holds)
    await make_engine(engine, RecordingBus(), steps.registry(*PLANNING)).run(task_id)

    ((guard, extended),) = seen
    assert extended
    assert (guard.task_id, guard.owner) == (task_id, WORKER)
