# ruff: noqa: F811 - the shared fixtures are imported by name and used as arguments
"""AC-14, end to end: the cancel route, the real engine and, for the stream, the real agent runtime
(WP-3.2f1 Task 7; TEST_STRATEGY AC-14; spec §16.7).

The route and the engine run in one event loop over one real PostgreSQL; the sandbox is a fake installed
through ``app.dependency_overrides[task_control_env]``. Every status is read from the database.

Timing: the engine beats every 0.1 s (``heartbeat_s``). The cancel flag is read by a beat, so the token fires
at most one beat plus one database round trip after the route returned. A test accepts ``BOUND_S`` = 2.0 s,
twenty beats of slack for a loaded machine; an engine that did not watch the flag would end only at the
step's own timeout (30 s) and fail the bound. Every wait is bounded; teardown cancels what is left.
"""

from __future__ import annotations

import asyncio
import functools
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.runtime import RuntimeConfig
from app.api.deps import AuditDep
from app.api.routes.tasks import task_control_env
from app.llm.pricing import PricingTable
from app.sandbox.client import FakeSandboxClient, SandboxUnavailable
from app.services import agent_runtime as runtime_module
from app.services.agent_step import AgentStep
from app.services.task_control import CANCEL_UNCONFIRMED_MESSAGE, TaskControlEnv
from app.workflow.engine import EngineOutcome, Step, StepContext, StepResult, TaskEngine
from tests.engine_runtime_fakes import implement_input, orchestrate, ready_world, retime
from tests.integration.test_engine import make_engine
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    make_user,
    owner,
    owner_id,
    signed_in,
    workspace_root,
)
from tests.integration.test_task_cancel_api import CANCEL, csrf, one_task, place
from tests.integration.test_tasks_api import open_gate, sql  # noqa: F401
from tests.runtime_fakes import FakeProviders, RecordingBus, SlowProvider, scripted, text_turn
from tests.security.test_runtime_gate import worker
from tests.usage_fakes import PRICING

WAIT_S = 10.0  # the bound of every wait of a test (a hang fails the test, never the run)
BOUND_S = 2.0  # cancel accepted -> the engine has written ``cancelled`` (see the module doc)


@pytest.fixture(autouse=True)
def fast_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime's own beat, patched before ``build_orchestrator`` (as in test_engine_runtime_cancel)."""
    monkeypatch.setattr(
        runtime_module,
        "RuntimeConfig",
        functools.partial(RuntimeConfig, heartbeat_interval_s=0.1, beat_timeout_s=1.0),
    )


@pytest.fixture
def pricing() -> PricingTable:
    return PRICING


@pytest.fixture
def install_sandbox(app: FastAPI) -> Iterator[Callable[..., FakeSandboxClient]]:
    """Installs a ``FakeSandboxClient`` behind the route; the override is removed at teardown."""

    def install(**kwargs: Any) -> FakeSandboxClient:
        fake = FakeSandboxClient(**kwargs)

        def env(audit: AuditDep) -> TaskControlEnv:
            return TaskControlEnv(sessions=app.state.dependencies.sessions, audit=audit, sandbox=fake)

        app.dependency_overrides[task_control_env] = env
        return fake

    yield install
    app.dependency_overrides.pop(task_control_env, None)


class WaitingStep:
    """A raw step that does nothing until its stop token fires, then notes when and returns done."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.token_seen_at: float | None = None

    async def __call__(self, ctx: StepContext) -> StepResult:
        self.entered.set()
        await asyncio.wait_for(ctx.cancel.wait(), timeout=30.0)  # the step's own limit, far above BOUND_S
        self.token_seen_at = time.monotonic()
        return StepResult.done()


class EnteredProvider(SlowProvider):
    """A ``SlowProvider`` that says when the stream was entered, so a test never infers it from timing."""

    def __init__(self, inner: Any, delay: float) -> None:
        super().__init__(inner, delay)
        self.entered = asyncio.Event()

    async def create_message(self, req: Any, **kwargs: Any) -> Any:
        self.entered.set()
        return await super().create_message(req, **kwargs)


async def audit_rows(engine: AsyncEngine, task_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT action, result, metadata FROM audit_events WHERE resource_id = :id "
                "AND (action LIKE 'task.cancel%' OR action = 'sandbox.run_killed') ORDER BY id"
            ),
            {"id": task_id},
        )
        return [(r.action, r.result, r.metadata) for r in rows]


async def task_events(engine: AsyncEngine, task_id: str) -> list[tuple[str, dict[str, Any]]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT type, payload FROM task_events WHERE task_id = :id ORDER BY seq"),
            {"id": uuid.UUID(task_id)},
        )
        return [(r.type, r.payload) for r in rows]


async def task_row(engine: AsyncEngine, task_id: str) -> Any:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT status, status_reason, cancel_requested, lease_owner, lease_expires_at "
                    "FROM tasks WHERE id = :id"
                ),
                {"id": uuid.UUID(task_id)},
            )
        ).one()


async def deliver(orch: TaskEngine, task_id: str) -> asyncio.Task[EngineOutcome]:
    return asyncio.create_task(orch.run(uuid.UUID(task_id)))


async def finish(runner: asyncio.Task[EngineOutcome], *, limit: float) -> EngineOutcome:
    """The delivery ends within ``limit`` and its outcome is returned; a hang fails the test."""
    done, _ = await asyncio.wait({runner}, timeout=limit)
    assert runner in done, "the engine delivery did not end within " + str(limit) + " s"
    return runner.result()


async def stop(runner: asyncio.Task[Any]) -> None:
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)


def cancelled_changes(events: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    return [p for kind, p in events if kind == "task.status_changed" and p["to"] == "cancelled"]


async def test_cancel_queued_immediate(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    install_sandbox: Callable[..., FakeSandboxClient],
) -> None:
    """A task without a live lease is cancelled by the route itself: no kill, one status event."""
    sandbox = install_sandbox(kill_returns=3)
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")

    response = await owner.post(CANCEL.format(task_id), json={"reason": "salah"}, headers=csrf(owner))

    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "cancelled"
    after = await task_row(engine, task_id)
    assert (after.status, after.status_reason) == ("cancelled", "cancel_requested")
    assert sandbox.killed_tasks == []
    assert [a for a, _, _ in await audit_rows(engine, task_id)] == ["task.cancel_requested"]
    assert cancelled_changes(await task_events(engine, task_id)) == [
        {"from": "queued", "to": "cancelled", "reason": "cancel_requested"}
    ]


async def test_cancel_running_kills_sandbox(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    install_sandbox: Callable[..., FakeSandboxClient],
) -> None:
    """A task the engine runs: the route sets the flag and kills the sandbox, a beat later the token fires
    and the engine itself writes ``cancelled``."""
    sandbox = install_sandbox(kill_returns=1)
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    step = WaitingStep()
    orch = make_engine(engine, RecordingBus(), {"implement": step}, heartbeat_s=0.1)
    runner = await deliver(orch, task_id)
    try:
        await asyncio.wait_for(step.entered.wait(), WAIT_S)

        response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
        accepted = time.monotonic()

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["status"] == "running" and data["cancel_requested"] is True
        assert sandbox.killed_tasks == [uuid.UUID(task_id)]
        assert await audit_rows(engine, task_id) == [
            ("task.cancel_requested", "success", {}),
            ("sandbox.run_killed", "success", {"killed": 1}),
        ]

        outcome = await finish(runner, limit=BOUND_S)
        ended = time.monotonic() - accepted
    finally:
        await stop(runner)

    assert outcome.stopped == "cancelled" and outcome.ran == ["implement"]
    assert step.token_seen_at is not None and step.token_seen_at - accepted <= BOUND_S
    assert ended <= BOUND_S
    after = await task_row(engine, task_id)
    assert (after.status, after.status_reason) == ("cancelled", "cancel_requested")
    assert (after.lease_owner, after.lease_expires_at) == (None, None)
    assert cancelled_changes(await task_events(engine, task_id)) == [
        {"from": "running", "to": "cancelled", "reason": "cancel_requested"}
    ]


async def test_cancel_during_llm_stream(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    install_sandbox: Callable[..., FakeSandboxClient],
    pricing: PricingTable,
) -> None:
    """The real runtime waits on a slow provider; the flag fires the token, the provider ends like a cut
    stream, the run is closed ``cancelled`` and the task is ``cancelled`` (not a limit, not abandoned)."""
    install_sandbox(kill_returns=0)
    settings = worker()
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    await ready_world(engine, settings)
    slow = EnteredProvider(scripted(text_turn()), 30.0)
    orch = retime(
        orchestrate(
            engine,
            settings,
            pricing,
            agent_steps={"implement": AgentStep("B14", implement_input)},
            providers=FakeProviders(slow),  # type: ignore[arg-type]
        ),
        heartbeat_s=0.1,
    )
    runner = await deliver(orch, task_id)
    try:
        await asyncio.wait_for(slow.entered.wait(), WAIT_S)  # the provider call is in flight

        response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))
        accepted = time.monotonic()
        assert response.status_code == 200, response.text

        outcome = await finish(runner, limit=BOUND_S)
        ended = time.monotonic() - accepted
    finally:
        await stop(runner)

    assert outcome.stopped == "cancelled", outcome
    assert ended <= BOUND_S
    assert await run_statuses(engine, task_id) == ["cancelled"]
    # the partial attempt is on the ledger: cancelled, with the prompt's tokens (10 in)
    assert await usage_rows(engine, task_id) == [("cancelled", "cancelled", 10, 0)]
    after = await task_row(engine, task_id)
    assert (after.status, after.status_reason) == ("cancelled", "cancel_requested")
    assert (after.lease_owner, after.lease_expires_at) == (None, None)


async def run_statuses(engine: AsyncEngine, task_id: str) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT status FROM agent_runs WHERE task_id = :t ORDER BY started_at, id"),
            {"t": uuid.UUID(task_id)},
        )
        return [r.status for r in rows]


type UsageRow = tuple[str, str | None, int | None, int | None]


async def usage_rows(engine: AsyncEngine, task_id: str) -> list[UsageRow]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT status, error_kind, input_tokens, output_tokens FROM usage_records "
                "WHERE task_id = :t ORDER BY created_at, id"
            ),
            {"t": uuid.UUID(task_id)},
        )
        return [(r.status, r.error_kind, r.input_tokens, r.output_tokens) for r in rows]


async def test_unkillable_marked(
    owner: httpx.AsyncClient,
    open_gate: None,
    engine: AsyncEngine,
    install_sandbox: Callable[..., FakeSandboxClient],
) -> None:
    """The sandbox cannot be reached: the route still answers 200, marks the task (event and audit) and the
    engine still ends the task ``cancelled``."""
    sandbox = install_sandbox(kill_error=SandboxUnavailable("the manager is down"))
    task_id = await one_task(owner)
    await place(engine, task_id, "queued")
    step = WaitingStep()
    steps: dict[str, Step] = {"implement": step}
    orch = make_engine(engine, RecordingBus(), steps, heartbeat_s=0.1)
    runner = await deliver(orch, task_id)
    try:
        await asyncio.wait_for(step.entered.wait(), WAIT_S)

        response = await owner.post(CANCEL.format(task_id), headers=csrf(owner))

        assert response.status_code == 200, response.text
        assert response.json()["data"]["cancel_requested"] is True
        assert sandbox.killed_tasks == [uuid.UUID(task_id)]
        outcome = await finish(runner, limit=BOUND_S)
    finally:
        await stop(runner)

    audits = await audit_rows(engine, task_id)
    assert audits == [
        ("task.cancel_requested", "success", {}),
        ("task.cancel_unconfirmed", "error", {"error_type": "SandboxUnavailable"}),
    ]
    marks = [p for kind, p in await task_events(engine, task_id) if kind == "task.log"]
    assert [(p["level"], p["code"], p["message"]) for p in marks] == [
        ("warning", "cancel_unconfirmed", CANCEL_UNCONFIRMED_MESSAGE)
    ]
    assert outcome.stopped == "cancelled"
    after = await task_row(engine, task_id)
    assert (after.status, after.lease_owner) == ("cancelled", None)
