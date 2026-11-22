"""Recovery sweep (ARCHITECTURE §5.3, RUNBOOK): orphaned running|validating tasks are interrupted.

Run recovery (WP-3.1f2b1, spec 3.1f2b Keputusan Lead #3-#8): ``agent_runs`` and ``tool_calls`` left
``running`` without a live worker are closed per task on the DB clock; a live worker's run is never touched.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.runtime import ERROR_TEXTS, RunEnd, RunStart
from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.money import decimal_str
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.llm.pricing import PricingTable
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task, TaskEvent
from app.models.tools import ToolCall
from app.services import run_recovery
from app.services.agent_runtime import AgentRunAlreadyFinished, AgentRunWriter
from app.services.agents import sync_registry
from app.services.run_recovery import RunRecoveryIncomplete, RunRecoveryResult, recover_runs
from app.workflow.recovery import recover_expired
from tests.conftest import reset_data
from tests.integration.test_agent_run_writer import (
    CACHED,
    CLOSE_CLOCK,
    MEASURED,
    closed_row,
    ledger_row,
    ledger_sum,
    task_events,
)
from tests.runtime_fakes import REGISTRY, RecordingBus

AUDIT = AuditWriter(Redactor())


class Recorder:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        self.sent.append(message)


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

    async def create(status: str, lease: str | None, *, cancel: bool = False) -> uuid.UUID:
        async with make_sessionmaker(engine)() as session:
            task = Task(
                project_id=project_id, title="t", prompt="p", workflow_type="create", created_by=owner_id
            )
            session.add(task)
            await session.flush()
            # Test setup: place the task directly (past the writer backstop).
            await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await session.execute(
                text(
                    "UPDATE tasks SET status = :s, cancel_requested = :c, lease_owner = 'w-old', "
                    "lease_expires_at = now() + CAST(CAST(:l AS text) AS interval) "
                    "WHERE id = :id"
                ),
                {"s": status, "c": cancel, "l": lease, "id": task.id},
            )
            await session.commit()
            return task.id

    return create


async def status_of(engine: AsyncEngine, task_id: uuid.UUID) -> tuple[str, str | None, Any]:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, status_reason, lease_owner FROM tasks WHERE id = :id"), {"id": task_id}
            )
        ).one()
    return row.status, row.status_reason, row.lease_owner


async def test_expired_lease_marks_interrupted(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    running = await make_task("running", "-5 seconds")
    validating = await make_task("validating", "-5 seconds")
    bus = Recorder()
    result = await recover_expired(make_sessionmaker(engine), bus, AUDIT)
    assert (result.candidates, result.recovered, result.failed, result.skipped) == (2, 2, 0, 0)
    for task_id in (running, validating):
        assert await status_of(engine, task_id) == ("interrupted", "lease_expired", None)
    assert {m["payload"]["to"] for m in bus.sent} == {"interrupted"}
    async with make_sessionmaker(engine)() as session:
        actors = (
            await session.scalars(
                select(AuditEvent.actor_id).where(
                    AuditEvent.action == "task.status_changed", AuditEvent.resource_id == str(running)
                )
            )
        ).all()
    assert actors == ["recovery"]


async def test_cancel_requested_becomes_cancelled(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    task_id = await make_task("running", "-5 seconds", cancel=True)
    await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert await status_of(engine, task_id) == ("cancelled", "cancel_requested", None)


async def test_live_leases_and_other_statuses_are_untouched(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    live = await make_task("running", "30 seconds")
    queued = await make_task("queued", "-5 seconds")
    fresh_no_lease = await make_task("running", None)  # just moved to running; its worker is claiming
    result = await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert result.recovered == 0
    assert (await status_of(engine, live))[0] == "running"
    assert (await status_of(engine, queued))[0] == "queued"
    assert (await status_of(engine, fresh_no_lease))[0] == "running"


async def test_active_task_without_a_lease_is_recovered_after_a_grace_period(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
) -> None:
    """Review WP-1.1f H2: running with lease NULL (released early / crash) was never recovered."""
    task_id = await make_task("running", None)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE tasks SET updated_at = now() - interval '2 minutes' WHERE id = :id"), {"id": task_id}
        )
    result = await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert result.recovered == 1
    assert (await status_of(engine, task_id))[:2] == ("interrupted", "lease_expired")


async def test_parallel_sweeps_transition_once(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    ids = [await make_task("running", "-5 seconds") for _ in range(5)]
    sessions = make_sessionmaker(engine)
    results = await asyncio.gather(*(recover_expired(sessions, Recorder(), AUDIT) for _ in range(3)))
    assert sum(r.recovered for r in results) == 5
    assert sum(r.failed for r in results) == 0  # SKIP LOCKED: losers skip, they do not hit VERSION_CONFLICT
    async with sessions() as session:
        per_task = (
            await session.execute(
                select(TaskEvent.task_id, func.count())
                .where(TaskEvent.task_id.in_(ids), TaskEvent.type == "task.status_changed")
                .group_by(TaskEvent.task_id)
            )
        ).all()
    assert sorted(count for _, count in per_task) == [1] * 5


async def test_one_failing_task_does_not_stop_the_sweep(  # type: ignore[no-untyped-def]
    engine: AsyncEngine,
    make_task,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.workflow import recovery

    bad = await make_task("running", "-5 seconds")
    good = await make_task("running", "-5 seconds")
    real = recovery.transition

    async def flaky(db, task, to, **kwargs):  # type: ignore[no-untyped-def]
        if task.id == bad:
            raise RuntimeError("boom")
        await real(db, task, to, **kwargs)

    monkeypatch.setattr(recovery, "transition", flaky)
    result = await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    assert (result.recovered, result.failed) == (1, 1)
    assert "task_recovery_failed" in capsys.readouterr().out
    assert (await status_of(engine, good))[0] == "interrupted"
    assert (await status_of(engine, bad))[0] == "running"


async def test_failed_task_logs_the_error_type_only(  # type: ignore[no-untyped-def]
    engine: AsyncEngine,
    make_task,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The log names the error type, never its text or a traceback (a driver error can carry a value)."""
    from app.workflow import recovery

    await make_task("running", "-5 seconds")

    async def boom(db, task, to, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    monkeypatch.setattr(recovery, "transition", boom)
    await recover_expired(make_sessionmaker(engine), Recorder(), AUDIT)
    out = capsys.readouterr().out
    assert "task_recovery_failed" in out and "RuntimeError" in out
    assert "boom" not in out
    assert "Traceback" not in out


async def seed_orphan(database_url: str) -> uuid.UUID:
    """Own engine: callers are synchronous tests that drive asyncio.run themselves."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    try:
        await reset_data(engine)
        async with make_sessionmaker(engine)() as session:
            owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
            session.add(owner)
            await session.flush()
            project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
            session.add(project)
            await session.flush()
            task = Task(
                project_id=project.id, title="t", prompt="p", workflow_type="create", created_by=owner.id
            )
            session.add(task)
            await session.flush()
            await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await session.execute(
                text(
                    "UPDATE tasks SET status = 'running', lease_owner = 'w', "
                    "lease_expires_at = now() - interval '5 seconds' WHERE id = :id"
                ),
                {"id": task.id},
            )
            await session.commit()
            return task.id
    finally:
        await engine.dispose()


async def read_status(database_url: str, task_id: uuid.UUID) -> str:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(text("SELECT status FROM tasks WHERE id = :id"), {"id": task_id})
            ).scalar_one()
    finally:
        await engine.dispose()


def test_cli_recover_reports_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], migrated_database: str
) -> None:
    from app.cli import main
    from tests.conftest import make_test_env

    task_id = asyncio.run(seed_orphan(migrated_database))
    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    assert main(["recover"]) == 0
    out = capsys.readouterr().out
    assert "candidates: 1, recovered: 1, failed: 0, skipped: 0" in out
    assert "runs_recovered: 0, tool_calls_closed: 0, runs_failed: 0, runs_skipped: 0" in out
    assert asyncio.run(read_status(migrated_database, task_id)) == "interrupted"


def test_celery_job_runs_the_sweep(monkeypatch: pytest.MonkeyPatch, migrated_database: str) -> None:
    from app.worker.jobs import recovery_sweep
    from tests.conftest import make_test_env

    task_id = asyncio.run(seed_orphan(migrated_database))
    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    assert recovery_sweep.apply().get() == {
        "candidates": 1,
        "recovered": 1,
        "failed": 0,
        "skipped": 0,
        "runs_recovered": 0,
        "tool_calls_closed": 0,
        "runs_failed": 0,
        "runs_skipped": 0,
    }
    assert asyncio.run(read_status(migrated_database, task_id)) == "interrupted"


async def test_duplicate_delivery_single_execution(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    """TEST_STRATEGY AC-17: a redelivered job for an already-claimed or finished task does not run it."""
    from app.workflow.lease import ClaimResult, claim_lease

    queued = await make_task("queued", None)
    finished = await make_task("completed", None)
    sessions = make_sessionmaker(engine)

    async def claim(task_id: uuid.UUID, worker: str) -> ClaimResult:
        async with sessions() as session:
            result = await claim_lease(session, task_id, worker, max_active=5)
            await session.commit()
            return result

    first, second = await asyncio.gather(claim(queued, "w1"), claim(queued, "w2"))
    assert sorted([first.value, second.value]) == ["acquired", "held"]
    assert await claim(finished, "w3") is ClaimResult.NOT_CLAIMABLE


def test_failed_recoveries_fail_the_celery_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import jobs
    from app.workflow.recovery import RecoveryIncomplete, RecoveryResult

    async def partial() -> dict[str, int]:
        raise RecoveryIncomplete(RecoveryResult(candidates=2, recovered=1, failed=1, skipped=0))

    monkeypatch.setattr(jobs, "run_recovery_sweep", partial)
    result = jobs.recovery_sweep.apply()
    assert result.failed()
    assert isinstance(result.result, RecoveryIncomplete)


def _patch_sweep_parts(
    monkeypatch: pytest.MonkeyPatch, tasks_failed: int, runs_failed: int, runs_skipped: int = 0
) -> list[str]:
    from app.worker import jobs
    from app.workflow.recovery import RecoveryResult

    calls: list[str] = []

    async def fake_tasks(*_a: object, **_k: object) -> RecoveryResult:
        calls.append("tasks")
        return RecoveryResult(candidates=1, recovered=1 - tasks_failed, failed=tasks_failed, skipped=0)

    async def fake_runs(*_a: object, **_k: object) -> RunRecoveryResult:
        calls.append("runs")
        return RunRecoveryResult(tasks=1, runs=2, tool_calls=3, failed=runs_failed, skipped=runs_skipped)

    monkeypatch.setattr(jobs, "recover_expired", fake_tasks)
    monkeypatch.setattr(jobs, "recover_runs", fake_runs)
    return calls


def test_failed_run_recoveries_fail_the_celery_job(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    from app.worker import jobs
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    calls = _patch_sweep_parts(monkeypatch, tasks_failed=0, runs_failed=1)
    result = jobs.recovery_sweep.apply()
    assert result.failed()
    assert isinstance(result.result, RunRecoveryIncomplete)
    assert result.result.result.failed == 1
    assert calls == ["tasks", "runs"]


def test_failed_task_recovery_does_not_starve_run_recovery(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    from app.worker import jobs
    from app.workflow.recovery import RecoveryIncomplete
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    calls = _patch_sweep_parts(monkeypatch, tasks_failed=1, runs_failed=1)
    result = jobs.recovery_sweep.apply()
    assert isinstance(result.result, RecoveryIncomplete)  # tasks first
    assert calls == ["tasks", "runs"]  # runs still ran


def test_cli_recover_fails_on_run_failures_and_prints_all_counts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], migrated_database: str
) -> None:
    from app.cli import main
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    _patch_sweep_parts(monkeypatch, tasks_failed=0, runs_failed=1)
    assert main(["recover"]) == 1
    out = capsys.readouterr().out
    assert "runs_recovered: 2, tool_calls_closed: 3, runs_failed: 1, runs_skipped: 0" in out


# Run recovery (WP-3.1f2b1, spec 3.1f2b Keputusan Lead #3-#8) ---------------------------------------------

LIVE, DEAD = "30 seconds", "-5 seconds"
MARGIN_S = 10  # each side of a stale bound (timeout + 60 s): a slow test run never crosses it (backend M3)
NOTHING = RunRecoveryResult(tasks=0, runs=0, tool_calls=0, failed=0, skipped=0)


@pytest.fixture
async def profiles(engine: AsyncEngine, make_task) -> None:  # type: ignore[no-untyped-def]
    """The registry's agent profiles, synced after ``make_task`` reset the data (a run needs its profile)."""
    async with make_sessionmaker(engine)() as session:
        await sync_registry(session, REGISTRY, AUDIT)
        await session.commit()


async def project_of(engine: AsyncEngine, task_id: uuid.UUID) -> uuid.UUID:
    async with engine.connect() as conn:
        query = text("SELECT project_id FROM tasks WHERE id = :id")
        return (await conn.execute(query, {"id": task_id})).scalar_one()


async def start_run(
    engine: AsyncEngine, task_id: uuid.UUID, *, age_s: int | None = None, agent_id: str = "B14"
) -> uuid.UUID:
    """A ``running`` row from the runtime's writer; ``age_s`` back-dates ``started_at`` on the DB clock."""
    run = RunStart(
        agent_run_id=uuid.uuid4(),
        task_id=task_id,
        project_id=await project_of(engine, task_id),
        agent_id=agent_id,
        agent_version=1,
        step="implement",
        model_provider="scripted",
        model_name="claude-haiku-5-5",
        prompt_version="0123456789ab",
        input={},
        start_token=uuid.uuid4(),
    )
    await AgentRunWriter(make_sessionmaker(engine), None, AUDIT).start(run, retry=False)
    if age_s is not None:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE agent_runs SET started_at = clock_timestamp() - make_interval(secs => :s) "
                    "WHERE id = :id"
                ),
                {"s": float(age_s), "id": run.agent_run_id},
            )
    return run.agent_run_id


async def set_profile_timeout(engine: AsyncEngine, seconds: int | None) -> None:
    """B14's ``limits.timeout_seconds``; ``None`` removes the key. The FK agent_runs -> agent_profiles keeps
    the profile row, so a run without a timeout is a profile whose ``limits`` lacks it (ledger ruling R1)."""
    async with engine.begin() as conn:
        if seconds is None:
            await conn.execute(
                text("UPDATE agent_profiles SET limits = limits - 'timeout_seconds' WHERE agent_id = 'B14'")
            )
        else:
            await conn.execute(
                text(
                    "UPDATE agent_profiles SET limits = jsonb_set(limits, '{timeout_seconds}', "
                    "to_jsonb(CAST(:t AS integer))) WHERE agent_id = 'B14'"
                ),
                {"t": seconds},
            )


async def running_call(
    engine: AsyncEngine,
    task_id: uuid.UUID,
    run_id: uuid.UUID | None,
    *,
    risk: int = 0,
    name: str = "repo.read_file",
) -> uuid.UUID:
    async with make_sessionmaker(engine)() as session:
        call = ToolCall(
            task_id=task_id,
            agent_run_id=run_id,
            agent_id="B14",
            tool_name=name,
            risk_level=risk,
            input_hash="a" * 64,
            sanitized_input={"path": "a.md"},
            status="running",
            tool_use_id="toolu_" + uuid.uuid4().hex,
        )
        session.add(call)
        await session.commit()
        return call.id


async def finish_owner(engine: AsyncEngine, run_id: uuid.UUID) -> dict[str, Any]:
    """What ``finish`` names of a row ``start_run`` wrote (spec WP-3.2 §16.6.2 #88): task, agent, token."""
    async with engine.connect() as conn:
        query = text("SELECT task_id, agent_id, start_token FROM agent_runs WHERE id = :id")
        task_id, agent_id, start_token = (await conn.execute(query, {"id": run_id})).one()
    return {"task_id": task_id, "agent_id": agent_id, "start_token": start_token}


async def run_row(engine: AsyncEngine, run_id: uuid.UUID) -> Row[Any]:
    async with engine.connect() as conn:
        query = text(
            "SELECT status, error_code, error, output, input_tokens, output_tokens, estimated_cost, "
            "cost_status, finished_at, duration_ms FROM agent_runs WHERE id = :id"
        )
        return (await conn.execute(query, {"id": run_id})).one()


async def call_state(engine: AsyncEngine, call_id: uuid.UUID) -> tuple[str, str | None]:
    async with engine.connect() as conn:
        query = text("SELECT status, error_code FROM tool_calls WHERE id = :id")
        status, code = (await conn.execute(query, {"id": call_id})).one()
    return status, code


async def test_run_of_a_task_without_a_live_lease_is_interrupted(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles, pricing: PricingTable
) -> None:
    task_id = await make_task("running", DEAD)
    run_id = await start_run(engine, task_id)  # just started: only the dead lease closes it
    sessions = make_sessionmaker(engine)
    project_id = await project_of(engine, task_id)
    await ledger_row(sessions, pricing, project_id, task_id, run_id)
    await ledger_row(sessions, pricing, project_id, task_id, run_id)
    ledger_input_sum = 200
    bus = RecordingBus(engine)

    result = await recover_runs(sessions, bus, AUDIT)

    assert result == RunRecoveryResult(tasks=1, runs=1, tool_calls=0, failed=0, skipped=0)
    row = await run_row(engine, run_id)
    assert (row.status, row.error_code, row.cost_status) == ("failed", "interrupted", "partial")
    assert row.input_tokens == ledger_input_sum and row.finished_at is not None
    assert row.output_tokens == 40 and row.duration_ms is not None and row.duration_ms >= 0
    assert row.error == ERROR_TEXTS["interrupted"] and row.output is None
    [message] = bus.of_type("agent.run_finished")
    event = message["payload"]
    assert event["status"] == "failed" and event["usage"]["cost_status"] == "partial"
    assert event == {
        "agent_run_id": str(run_id),
        "agent_id": "B14",
        "status": "failed",
        "error_code": "interrupted",
        "usage": {
            "input_tokens": ledger_input_sum,
            "output_tokens": 40,
            "estimated_cost": decimal_str(row.estimated_cost),
            "cost_status": "partial",
        },
    }
    assert bus.unseen == []  # published only after the commit
    assert (await status_of(engine, task_id))[0] == "running"  # the task is task recovery's, never moved here
    async with sessions() as session:
        audited = (
            await session.scalars(select(AuditEvent.id).where(AuditEvent.resource_id == str(run_id)))
        ).all()
    assert audited == []  # Keputusan #6: a recovered run is not audited on its own


async def test_run_recovery_locks_the_task_row_in_a_fully_bound_transaction(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unit's first statement bounds lock, statement and idle-in-transaction time (spec §16.6.2 #86)."""
    task_id = await make_task("running", DEAD)
    await start_run(engine, task_id)
    seen: list[dict[str, Any]] = []
    original = run_recovery.lock_task_row

    async def spy(db: Any, locked: uuid.UUID) -> Any:
        settings = {}
        for key, name in (
            ("lock", "lock_timeout"),
            ("statement", "statement_timeout"),
            ("idle", "idle_in_transaction_session_timeout"),
        ):
            settings[key] = await db.scalar(text(f"SELECT current_setting('{name}')"))
        seen.append(settings)
        return await original(db, locked)

    monkeypatch.setattr(run_recovery, "lock_task_row", spy)

    await recover_runs(make_sessionmaker(engine), RecordingBus(engine), AUDIT)

    assert seen == [{"lock": "5s", "statement": "10s", "idle": "30s"}]


@pytest.mark.parametrize("rows", [2, 0], ids=["with-ledger-rows", "no-ledger-rows"])
async def test_the_recovery_close_writes_this_row_and_this_event(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles, pricing: PricingTable, rows: int
) -> None:
    """Characterisation (WP-3.2c2a Task 1): the recovery's close, the row and ``agent.run_finished`` it
    writes, before the three closers share ``close_run_row``. Usage is never complete (the known sum,
    ``partial``; no rows: unknown), ``output`` and ``iterations`` stay as the running row has them (SQL NULL,
    0), ``duration_ms`` is measured on the DB clock."""
    task_id = await make_task("running", DEAD)
    run_id = await start_run(engine, task_id, age_s=7)
    sessions = make_sessionmaker(engine)
    project_id = await project_of(engine, task_id)
    for _ in range(rows):
        await ledger_row(sessions, pricing, project_id, task_id, run_id, usage=CACHED)
    bus = RecordingBus(engine)

    assert await recover_runs(sessions, bus, AUDIT) == RunRecoveryResult(
        tasks=1, runs=1, tool_calls=0, failed=0, skipped=0
    )

    cost = await ledger_sum(engine, run_id) if rows else None
    tokens = (100 * rows, 20 * rows, 5 * rows, 3 * rows) if rows else (None,) * 4
    status = "partial" if rows else "unavailable"
    assert await closed_row(engine, run_id) == {
        "task_id": task_id,
        "agent_id": "B14",
        "input": {},
        "status": "failed",
        "error_code": "interrupted",
        "error": "the run was interrupted: its worker is gone",
        "output": None,
        "output_type": None,
        "iterations": 0,
        "input_tokens": tokens[0],
        "output_tokens": tokens[1],
        "cache_read_tokens": tokens[2],
        "cache_creation_tokens": tokens[3],
        "estimated_cost": cost,
        "cost_status": status,
        "finished_at": CLOSE_CLOCK,
        "duration_ms": MEASURED,
    }
    finished = {
        "agent_run_id": str(run_id),
        "agent_id": "B14",
        "status": "failed",
        "error_code": "interrupted",
        "usage": {
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "estimated_cost": decimal_str(cost) if cost is not None else None,
            "cost_status": status,
        },
    }
    started = {"agent_run_id": str(run_id), "agent_id": "B14", "status": "running"}
    assert await task_events(engine, task_id) == [
        ("agent.run_started", started),
        ("agent.run_finished", finished),
    ]
    assert [(m["type"], m["payload"]) for m in bus.messages] == [("agent.run_finished", finished)]
    assert bus.messages[0]["seq"] is not None and bus.unseen == []  # committed, then published


async def test_recovered_run_without_ledger_rows_reports_unavailable_usage(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    task_id = await make_task("running", DEAD)
    run_id = await start_run(engine, task_id)
    bus = RecordingBus()
    await recover_runs(make_sessionmaker(engine), bus, AUDIT)
    row = await run_row(engine, run_id)
    assert row.cost_status == "unavailable" and row.input_tokens is None
    assert (row.status, row.error_code, row.output_tokens, row.estimated_cost) == (
        "failed",
        "interrupted",
        None,
        None,
    )
    [message] = bus.of_type("agent.run_finished")
    assert message["payload"]["usage"] == {
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost": None,
        "cost_status": "unavailable",
    }


async def test_live_run_under_a_live_lease_is_untouched_with_its_running_tool_call(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    task_id = await make_task("running", LIVE)
    run_id = await start_run(engine, task_id)
    call_id = await running_call(engine, task_id, run_id, risk=1, name="repo.write_file")
    bus = RecordingBus()
    assert await recover_runs(make_sessionmaker(engine), bus, AUDIT) == NOTHING
    assert (await run_row(engine, run_id)).status == "running"
    assert await call_state(engine, call_id) == ("running", None)
    assert bus.messages == []


async def test_stale_run_under_a_live_lease_is_interrupted(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    await set_profile_timeout(engine, 300)
    task_id = await make_task("running", LIVE)
    run_id = await start_run(engine, task_id, age_s=300 + 60 + MARGIN_S)
    result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    assert result == RunRecoveryResult(tasks=1, runs=1, tool_calls=0, failed=0, skipped=0)
    row = await run_row(engine, run_id)
    assert (row.status, row.error_code) == ("failed", "interrupted")
    assert row.duration_ms is not None and row.duration_ms >= (360 + MARGIN_S) * 1000  # from started_at


async def test_run_inside_its_bound_is_not_stale(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    await set_profile_timeout(engine, 300)
    task_id = await make_task("running", LIVE)
    run_id = await start_run(engine, task_id, age_s=300 + 60 - MARGIN_S)
    assert await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT) == NOTHING
    assert (await run_row(engine, run_id)).status == "running"


async def test_run_without_a_profile_timeout_uses_3600_seconds(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    await set_profile_timeout(engine, None)
    task_id = await make_task("running", LIVE)
    inside = await start_run(engine, task_id, age_s=3600 + 60 - MARGIN_S)
    past = await start_run(engine, task_id, age_s=3600 + 60 + MARGIN_S)
    result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    assert result == RunRecoveryResult(tasks=1, runs=1, tool_calls=0, failed=0, skipped=0)
    assert (await run_row(engine, inside)).status == "running"  # not "always stale"
    assert (await run_row(engine, past)).error_code == "interrupted"  # not "never stale"


async def test_tool_calls_of_a_recovered_run_close_unknown(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    """Under a live lease the calls close only because their run is closed in the same transaction (#8 b)."""
    await set_profile_timeout(engine, 300)
    task_id = await make_task("running", LIVE)
    run_id = await start_run(engine, task_id, age_s=300 + 60 + MARGIN_S)
    r0 = await running_call(engine, task_id, run_id, risk=0, name="repo.read_file")
    r1 = await running_call(engine, task_id, run_id, risk=1, name="repo.write_file")
    bus = RecordingBus(engine)
    result = await recover_runs(make_sessionmaker(engine), bus, AUDIT)
    assert result == RunRecoveryResult(tasks=1, runs=1, tool_calls=2, failed=0, skipped=0)
    assert await call_state(engine, r0) == ("error", "outcome_unknown")
    assert await call_state(engine, r1) == ("error", "outcome_unknown")
    async with make_sessionmaker(engine)() as session:
        audited = (
            await session.execute(
                select(AuditEvent.resource_id, AuditEvent.actor_type, AuditEvent.actor_id).where(
                    AuditEvent.action == "tool.executed", AuditEvent.resource_id.in_([str(r0), str(r1)])
                )
            )
        ).all()
    assert [tuple(a) for a in audited] == [(str(r1), "system", "recovery")]  # R1+ only
    finished = {m["payload"]["tool_call_id"] for m in bus.of_type("tool.call_finished")}
    assert finished == {str(r0), str(r1)}
    assert bus.unseen == []


async def test_a_recovered_calls_audit_names_its_code_and_its_run(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    """AG-13 INFO-1: the audit of a recovered R1+ call carries ``error_code`` and, for a call of a run, the
    run's correlation id, like the gateway's own audit of the call (``agent_run:<id>``)."""
    task_id = await make_task("running", DEAD)
    run_id = await start_run(engine, task_id)
    call_id = await running_call(engine, task_id, run_id, risk=1, name="repo.write_file")
    await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    async with make_sessionmaker(engine)() as session:
        audit = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "tool.executed", AuditEvent.resource_id == str(call_id)
            )
        )
    assert audit is not None
    assert audit.metadata_["error_code"] == "outcome_unknown"
    assert audit.correlation_id == f"agent_run:{run_id}"


async def test_running_tool_call_of_a_finished_run_closes_under_a_live_lease(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    task_id = await make_task("running", LIVE)
    run_id = await start_run(engine, task_id)
    call_id = await running_call(engine, task_id, run_id)
    await AgentRunWriter(make_sessionmaker(engine), None, AUDIT).finish(
        run_id, RunEnd("succeeded", 1, output={}), **await finish_owner(engine, run_id)
    )
    result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    assert result == RunRecoveryResult(tasks=1, runs=0, tool_calls=1, failed=0, skipped=0)
    assert await call_state(engine, call_id) == ("error", "outcome_unknown")
    row = await run_row(engine, run_id)
    assert (row.status, row.error_code) == ("succeeded", None)  # the runtime's end stays


async def test_running_tool_call_without_a_run_on_a_dead_lease_closes(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    dead = await make_task("running", DEAD)
    live = await make_task("running", LIVE)
    orphan = await running_call(engine, dead, None)
    engine_call = await running_call(engine, live, None)  # an engine call (WP-3.2) whose worker is alive
    result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    assert result == RunRecoveryResult(tasks=1, runs=0, tool_calls=1, failed=0, skipped=0)
    assert await call_state(engine, orphan) == ("error", "outcome_unknown")
    assert await call_state(engine, engine_call) == ("running", None)


async def test_running_tool_call_without_a_run_on_a_task_without_a_lease_closes(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    """Spec #4a/#8a, accepted (silent L5): a NULL lease is a dead lease, with no grace (unlike task recovery's
    ``NO_LEASE_GRACE``). A runless call (an engine call, WP-3.2) on a task without a lease is closed: WP-3.2
    holds a live lease while any tool runs (ARCHITECTURE §5.3)."""
    task_id = await make_task("running", None)  # lease_expires_at NULL
    call_id = await running_call(engine, task_id, None, risk=1, name="repo.write_file")
    result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    assert result == RunRecoveryResult(tasks=1, runs=0, tool_calls=1, failed=0, skipped=0)
    assert await call_state(engine, call_id) == ("error", "outcome_unknown")


async def test_parallel_run_sweeps_close_each_row_once(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    tasks = [await make_task("running", DEAD) for _ in range(4)]
    calls = []
    for task_id in tasks:
        run_id = await start_run(engine, task_id)
        calls.append(str(await running_call(engine, task_id, run_id, risk=1, name="repo.write_file")))
    sessions = make_sessionmaker(engine)
    buses = [RecordingBus(), RecordingBus()]
    results = await asyncio.gather(*(recover_runs(sessions, bus, AUDIT) for bus in buses))
    assert sum(r.runs for r in results) == 4 and sum(r.tool_calls for r in results) == 4
    assert sum(r.failed for r in results) == 0
    async with sessions() as session:
        per_row = (
            await session.execute(
                select(TaskEvent.task_id, TaskEvent.type, func.count())
                .where(
                    TaskEvent.task_id.in_(tasks),
                    TaskEvent.type.in_(["agent.run_finished", "tool.call_finished"]),
                )
                .group_by(TaskEvent.task_id, TaskEvent.type)
            )
        ).all()
        audits = await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "tool.executed", AuditEvent.resource_id.in_(calls))
        )
    assert sorted(count for *_, count in per_row) == [1] * 8  # one end per run and per call, per task
    assert audits == 4
    assert len([m for bus in buses for m in bus.messages]) == 8


async def test_runtime_finish_after_recovery_is_already_finished(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    task_id = await make_task("running", DEAD)
    run_id = await start_run(engine, task_id)
    sessions = make_sessionmaker(engine)
    writer = AgentRunWriter(sessions, None, AUDIT)
    await recover_runs(sessions, RecordingBus(), AUDIT)
    end = RunEnd("succeeded", 1, output={"summary": "late"})
    with pytest.raises(AgentRunAlreadyFinished):
        await writer.finish(run_id, end, **await finish_owner(engine, run_id))
    row = await run_row(engine, run_id)
    assert row.error_code == "interrupted"
    assert (row.status, row.output) == ("failed", None)
    async with sessions() as session:
        ends = await session.scalar(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.type == "agent.run_finished")
        )
    assert ends == 1


@pytest.mark.parametrize("recovery_first", [False, True])
async def test_runtime_finish_racing_a_recovery_ends_the_run_once(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles, recovery_first: bool
) -> None:
    """The first write wins; the other skips (recovery) or raises ``AgentRunAlreadyFinished`` (runtime)."""
    task_id = await make_task("running", DEAD)  # the lease lapsed while the worker was still finishing
    run_id = await start_run(engine, task_id)
    sessions = make_sessionmaker(engine)
    finishing = AgentRunWriter(sessions, None, AUDIT).finish(
        run_id, RunEnd("succeeded", 1, output={}), **await finish_owner(engine, run_id)
    )
    recovering = recover_runs(sessions, RecordingBus(), AUDIT)
    if recovery_first:
        recovery, finish = await asyncio.gather(recovering, finishing, return_exceptions=True)
    else:
        finish, recovery = await asyncio.gather(finishing, recovering, return_exceptions=True)
    assert isinstance(recovery, RunRecoveryResult) and recovery.failed == 0
    row = await run_row(engine, run_id)
    if isinstance(finish, AgentRunAlreadyFinished):
        assert (recovery.runs, row.status, row.error_code) == (1, "failed", "interrupted")
    else:
        assert not isinstance(finish, BaseException), finish
        assert (recovery.runs, row.status, row.error_code) == (0, "succeeded", None)
    async with sessions() as session:
        ends = await session.scalar(
            select(func.count())
            .select_from(TaskEvent)
            .where(TaskEvent.task_id == task_id, TaskEvent.type == "agent.run_finished")
        )
    assert ends == 1


@pytest.mark.parametrize("raw", ['"abc"', "100000"])
async def test_a_bad_profile_timeout_uses_3600_seconds_and_stops_nothing(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles, raw: str
) -> None:
    """Ruling R3: a hand-edited ``timeout_seconds`` (not a number, or above the registry's ceiling) falls back
    to or is clamped at 3600 s; it never fails the candidate query of the whole sweep."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE agent_profiles SET limits = jsonb_set(limits, '{timeout_seconds}', "
                "CAST(:v AS jsonb)) WHERE agent_id = 'B14'"
            ),
            {"v": raw},
        )
    other = await make_task("running", DEAD)
    other_run = await start_run(engine, other, agent_id="A02")  # another task, another (valid) profile
    task_id = await make_task("running", LIVE)
    inside = await start_run(engine, task_id, age_s=3600 + 60 - MARGIN_S)
    past = await start_run(engine, task_id, age_s=3600 + 60 + MARGIN_S)
    result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    assert result == RunRecoveryResult(tasks=2, runs=2, tool_calls=0, failed=0, skipped=0)
    assert (await run_row(engine, other_run)).error_code == "interrupted"
    assert (await run_row(engine, inside)).status == "running"
    assert (await run_row(engine, past)).error_code == "interrupted"


async def test_a_run_older_than_the_int4_milliseconds_still_closes(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    """``duration_ms`` is int4 (±24.8 days in ms): an older orphan closes with the duration clamped, instead
    of failing its task on every sweep."""
    task_id = await make_task("running", DEAD)
    run_id = await start_run(engine, task_id, age_s=25 * 24 * 3600)
    result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    assert result == RunRecoveryResult(tasks=1, runs=1, tool_calls=0, failed=0, skipped=0)
    row = await run_row(engine, run_id)
    assert (row.status, row.error_code, row.duration_ms) == ("failed", "interrupted", 2_147_483_647)


async def test_one_failing_task_does_not_stop_the_run_sweep(  # type: ignore[no-untyped-def]
    engine: AsyncEngine,
    make_task,
    profiles,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = await make_task("running", DEAD)
    good = await make_task("running", DEAD)
    bad_run, other_run = await start_run(engine, bad), await start_run(engine, good)
    await running_call(engine, bad, bad_run)
    await running_call(engine, good, other_run)
    real = run_recovery.close_abandoned_calls
    detail = "kk-detail-" + "d" * 12  # an exception text: never logged

    async def flaky(session, audit, calls, *, project_id):  # type: ignore[no-untyped-def]
        if any(call.task_id == bad for call in calls):
            raise RuntimeError("boom " + detail)
        return await real(session, audit, calls, project_id=project_id)

    monkeypatch.setattr(run_recovery, "close_abandoned_calls", flaky)
    result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
    other_row = await run_row(engine, other_run)
    assert result.failed == 1 and other_row.status == "failed"
    assert result == RunRecoveryResult(tasks=2, runs=1, tool_calls=1, failed=1, skipped=0)
    assert (await run_row(engine, bad_run)).status == "running"  # its run's close rolled back with the task
    out = capsys.readouterr().out
    assert "run_recovery_failed" in out and "RuntimeError" in out
    assert detail not in out  # logged by type only


def test_run_recovery_incomplete_is_rebuilt_from_its_result() -> None:
    """Celery re-creates a task's exception from ``__reduce__`` (like ``RecoveryIncomplete``)."""
    result = RunRecoveryResult(tasks=3, runs=1, tool_calls=2, failed=1, skipped=0)
    rebuild, args = RunRecoveryIncomplete(result).__reduce__()
    rebuilt = rebuild(*args)
    assert isinstance(rebuilt, RunRecoveryIncomplete) and rebuilt.result == result
    assert "1 of 3" in str(rebuilt)


def test_run_recovery_reads_only_the_db_clock() -> None:
    """Every time comparison is ``clock_timestamp()`` in SQL: the module never imports a worker clock."""
    tree = ast.parse(inspect.getsource(run_recovery))
    modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    modules |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {m.split(".")[0] for m in modules} & {"datetime", "time"}
    assert "clock_timestamp" in inspect.getsource(run_recovery)


# Final fix wave (WP-3.1f2b1 F5, F7) ------------------------------------------------------------------------


async def test_a_busy_task_is_skipped_reported_and_recovered_later(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    """Silent M1: a task row another transaction holds is skipped, counted and logged, never hidden behind a
    healthy-looking idle sweep. A skip does not fail the sweep; the next one closes the run."""
    from structlog.testing import capture_logs

    task_id = await make_task("running", DEAD)
    run_id = await start_run(engine, task_id)
    sessions = make_sessionmaker(engine)
    async with engine.connect() as holder:
        transaction = await holder.begin()
        await holder.execute(text("SELECT id FROM tasks WHERE id = :id FOR UPDATE"), {"id": task_id})
        with capture_logs() as logs:
            result = await recover_runs(sessions, RecordingBus(), AUDIT)
        await transaction.rollback()
    assert result == RunRecoveryResult(tasks=1, runs=0, tool_calls=0, failed=0, skipped=1)
    assert (await run_row(engine, run_id)).status == "running"
    [skipped] = [log for log in logs if log["event"] == "run_recovery_skipped"]
    assert (skipped["log_level"], skipped["task_id"], skipped["reason"]) == (
        "warning",
        str(task_id),
        "task_locked",
    )
    later = await recover_runs(sessions, RecordingBus(), AUDIT)
    assert later == RunRecoveryResult(tasks=1, runs=1, tool_calls=0, failed=0, skipped=0)


async def test_held_run_and_tool_call_rows_are_skipped_and_reported(  # type: ignore[no-untyped-def]
    engine: AsyncEngine, make_task, profiles
) -> None:
    """Silent M1: inside a task, a candidate run or tool call row that ``SKIP LOCKED`` passed over is counted
    as skipped (and logged with the task); the rows it could lock are still closed."""
    from structlog.testing import capture_logs

    task_id = await make_task("running", DEAD)
    held_run, free_run = await start_run(engine, task_id), await start_run(engine, task_id)
    held_call = await running_call(engine, task_id, None)
    async with engine.connect() as holder:
        transaction = await holder.begin()
        await holder.execute(text("SELECT id FROM agent_runs WHERE id = :id FOR UPDATE"), {"id": held_run})
        await holder.execute(text("SELECT id FROM tool_calls WHERE id = :id FOR UPDATE"), {"id": held_call})
        with capture_logs() as logs:
            result = await recover_runs(make_sessionmaker(engine), RecordingBus(), AUDIT)
        await transaction.rollback()
    assert result == RunRecoveryResult(tasks=1, runs=1, tool_calls=0, failed=0, skipped=2)
    assert (await run_row(engine, free_run)).error_code == "interrupted"
    assert (await run_row(engine, held_run)).status == "running"
    assert await call_state(engine, held_call) == ("running", None)
    [skipped] = [log for log in logs if log["event"] == "run_recovery_skipped"]
    assert (skipped["task_id"], skipped["reason"], skipped["runs"], skipped["tool_calls"]) == (
        str(task_id),
        "rows_locked",
        1,
        1,
    )


def test_skipped_rows_never_fail_the_job_and_are_counted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], migrated_database: str
) -> None:
    from app.cli import main
    from app.worker import jobs
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    _patch_sweep_parts(monkeypatch, tasks_failed=0, runs_failed=0, runs_skipped=2)
    result = jobs.recovery_sweep.apply()
    assert result.successful() and result.get()["runs_skipped"] == 2
    assert main(["recover"]) == 0
    assert "runs_failed: 0, runs_skipped: 2" in capsys.readouterr().out


def _patch_raising_phases(
    monkeypatch: pytest.MonkeyPatch,
    tasks: BaseException | int,
    runs: BaseException | int,
) -> list[str]:
    """``recover_expired``/``recover_runs`` that raise the given exception, or return a result with that many
    failures."""
    from app.worker import jobs
    from app.workflow.recovery import RecoveryResult

    calls: list[str] = []

    async def fake_tasks(*_a: object, **_k: object) -> RecoveryResult:
        calls.append("tasks")
        if isinstance(tasks, BaseException):
            raise tasks
        return RecoveryResult(candidates=1, recovered=1 - tasks, failed=tasks, skipped=0)

    async def fake_runs(*_a: object, **_k: object) -> RunRecoveryResult:
        calls.append("runs")
        if isinstance(runs, BaseException):
            raise runs
        return RunRecoveryResult(tasks=1, runs=1 - runs, tool_calls=0, failed=runs, skipped=0)

    monkeypatch.setattr(jobs, "recover_expired", fake_tasks)
    monkeypatch.setattr(jobs, "recover_runs", fake_runs)
    return calls


def test_a_raising_task_phase_never_starves_run_recovery(
    monkeypatch: pytest.MonkeyPatch, migrated_database: str
) -> None:
    """Backend M2 / silent L3 (ruling R8): an exception of task recovery is held; run recovery still runs,
    then the task phase's exception is raised (the job fails loudly)."""
    from app.worker import jobs
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    boom = RuntimeError("task phase down")
    calls = _patch_raising_phases(monkeypatch, tasks=boom, runs=0)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(jobs.run_recovery_sweep())
    assert raised.value is boom
    assert calls == ["tasks", "runs"]


def test_a_hung_task_phase_times_out_alone(monkeypatch: pytest.MonkeyPatch, migrated_database: str) -> None:
    """Ruling R8: each phase has its own half of the sweep's timeout; a hung task phase times out and run
    recovery still gets its own half. The margins are wide (re-review N6): the task phase would sleep 60x its
    half, and the run phase needs under a third of its own half, but more than a shared budget would leave
    it (nothing)."""
    from app.worker import jobs
    from app.workflow.recovery import RecoveryResult
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(jobs, "SWEEP_TIMEOUT_SECONDS", 2.0)  # 1 s per phase
    calls: list[str] = []

    async def hung_tasks(*_a: object, **_k: object) -> RecoveryResult:
        calls.append("tasks")
        await asyncio.sleep(60)
        raise AssertionError("not reached")

    async def slow_runs(*_a: object, **_k: object) -> RunRecoveryResult:
        await asyncio.sleep(0.3)  # inside its own 1 s, though the task phase used all of its time
        calls.append("runs")
        return RunRecoveryResult(tasks=0, runs=0, tool_calls=0, failed=0, skipped=0)

    monkeypatch.setattr(jobs, "recover_expired", hung_tasks)
    monkeypatch.setattr(jobs, "recover_runs", slow_runs)
    with pytest.raises(TimeoutError):
        asyncio.run(jobs.run_recovery_sweep())
    assert calls == ["tasks", "runs"]


@pytest.mark.parametrize(
    ("tasks", "runs", "raised"),
    [
        (RuntimeError("tasks"), ValueError("runs"), RuntimeError),  # the task phase's exception first
        (1, ValueError("runs"), "RecoveryIncomplete"),  # then RecoveryIncomplete
        (0, ValueError("runs"), ValueError),  # then the run phase's exception
        (0, 1, "RunRecoveryIncomplete"),  # then RunRecoveryIncomplete
    ],
)
def test_the_sweep_raises_in_precedence(
    monkeypatch: pytest.MonkeyPatch,
    migrated_database: str,
    tasks: BaseException | int,
    runs: BaseException | int,
    raised: type[BaseException] | str,
) -> None:
    from app.worker import jobs
    from app.workflow.recovery import RecoveryIncomplete
    from tests.conftest import make_test_env

    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)
    calls = _patch_raising_phases(monkeypatch, tasks=tasks, runs=runs)
    expected = {"RecoveryIncomplete": RecoveryIncomplete, "RunRecoveryIncomplete": RunRecoveryIncomplete}
    kind = expected[raised] if isinstance(raised, str) else raised
    with pytest.raises(Exception) as info:  # the exact type is checked below
        asyncio.run(jobs.run_recovery_sweep())
    assert type(info.value) is kind
    assert calls == ["tasks", "runs"]
