# ruff: noqa: F811 - make_task, the fixture of test_engine, is imported by name and used as an argument
"""The engine over the real agent runtime when things go wrong (WP-3.2c2c2 Task 7; spec §16.6.5, §16.6.6).

Cancel and lease loss (N34, C2 ``CancelledError``), lost acks of the run writer (BC-24, N59), the lease that
must lapse when the engine gives up on a step that does not stop (N53, AG-13 C2: overrun, give-up, outer
cancel; a control that does not revoke shows the lease would otherwise be kept alive) and the drain of a
delivery (N54). The runtime is the real one (``build_orchestrator``); only the provider is scripted. Every
status is read from the database.

Timing: the runtime beats every 0.1 s (``RuntimeConfig`` patched before the build), the lease lasts 2 s
(patched after the build, so the ports keep their defaults until ``retime``). Margins: a lease must lapse
2.0 s after the last renewal, a renewal precedes the revocation by at most one beat (0.1 s), and the test
accepts 2.5 s (0.4 s of slack for a loaded machine); the control watches 6 s (three lease periods) for
more than 10 renewals, where a live heartbeat makes about 60. Every wait is bounded.
"""

from __future__ import annotations

import asyncio
import functools
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.agents.runtime import RuntimeConfig
from app.core.audit import AuditWriter
from app.core.cancel import CancelToken
from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.llm.base import LLMError, LLMRequest, LLMResponse, TextDeltaHandler
from app.llm.pricing import PricingTable, load_pricing
from app.services import agent_runtime as runtime_module
from app.services.agent_runtime import AgentRunWriter
from app.services.agent_step import AgentStep, DbAgentStepReads
from app.services.run_recovery import recover_runs
from app.worker import jobs
from app.worker import pricing as pricing_module
from app.worker import registry as registry_module
from app.worker.identity import delivery_owner
from app.workflow import engine as engine_module
from app.workflow import lease as lease_module
from app.workflow.engine import TaskEngine
from app.workflow.recovery import recover_expired
from tests.conftest import make_test_env
from tests.engine_runtime_fakes import (
    Renewals,
    SwallowingProvider,
    implement_input,
    intake_input,
    orchestrate,
    plan_input,
    raw_done,
    ready_world,
    retime,
    to_planned,
)
from tests.integration.test_engine import (
    changed,
    events,
    finished,
    kinds,
    make_task,  # noqa: F401 - a fixture, used by name as an argument
    other_tasks,
    row,
    started,
    take_over_lease,
)
from tests.integration.test_engine_runtime import lease_free, runs
from tests.runtime_fakes import REGISTRY, FakeProviders, RecordingBus
from tests.security.test_runtime_gate import worker
from tests.usage_fakes import PRICING

WAIT_S = 10.0  # the bound of every wait of a test (a hang fails the test, never the run)


@pytest.fixture(autouse=True)
def fast_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """BC-5: the runtime's own beat and its one-try timeout, patched before ``build_orchestrator``."""
    monkeypatch.setattr(
        runtime_module,
        "RuntimeConfig",
        functools.partial(RuntimeConfig, heartbeat_interval_s=0.1, beat_timeout_s=1.0),
    )


@pytest.fixture
def pricing() -> PricingTable:
    return PRICING


class SlowProvider:
    """A provider that takes ``seconds`` and stops at once when cancelled (a well-behaved one)."""

    name = "scripted"

    def __init__(self, seconds: float = 5.0) -> None:
        self.entered = asyncio.Event()
        self.seconds = seconds

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        self.entered.set()
        await asyncio.sleep(self.seconds)
        raise LLMError("cancelled")

    async def aclose(self) -> None:
        return None


async def until(predicate: Callable[[], bool], what: str, *, limit: float = WAIT_S) -> None:
    deadline = asyncio.get_running_loop().time() + limit
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out waiting for " + what)
        await asyncio.sleep(0.02)


async def settled(runner: asyncio.Task[Any]) -> None:
    """The runner is over within the bound (and its outcome is read by the test, not lost)."""
    done, _ = await asyncio.wait({runner}, timeout=WAIT_S)
    assert runner in done, "the engine run did not end"


async def lapsed(engine: AsyncEngine, task_id: uuid.UUID) -> bool:
    async with engine.connect() as conn:
        return bool(
            await conn.scalar(
                text("SELECT lease_expires_at < clock_timestamp() FROM tasks WHERE id = :id"),
                {"id": task_id},
            )
        )


class Revocations:
    """The engine's ``revoke_lease_owner``, wrapped to note when it was called. The control does not call the
    real one, so the owner stays valid and the runtime's beat keeps renewing (a control that must differ)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, real: bool) -> None:
        self.at: list[float] = []
        original = engine_module.revoke_lease_owner

        def wrapper(owner: str) -> None:
            if real:
                original(owner)
            self.at.append(time.monotonic())

        monkeypatch.setattr(engine_module, "revoke_lease_owner", wrapper)


def spy_outcome_reads(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """The run ids the adapter reads back from the database."""
    read: list[uuid.UUID] = []
    original = DbAgentStepReads.run_outcome

    async def spy(self: DbAgentStepReads, agent_run_id: uuid.UUID, **kwargs: Any) -> Any:
        read.append(agent_run_id)
        return await original(self, agent_run_id, **kwargs)

    monkeypatch.setattr(DbAgentStepReads, "run_outcome", spy)
    return read


def logged(logs: Sequence[Mapping[str, Any]], event: str) -> list[Mapping[str, Any]]:
    return [e for e in logs if e["event"] == event]


# --- N34: the lease is taken over while the real runtime runs -------------------------------------------


async def test_a_lease_taken_over_mid_run_stops_the_step_and_closes_the_run(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    pricing: PricingTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N34: another worker takes the lease; the engine's beat sees it and cancels the step, the runtime
    closes its run before the cancel goes on, and nothing is written for the step or the task."""
    settings = worker()
    task_id = await make_task("queued")
    await ready_world(engine, settings)
    provider = SlowProvider(5.0)
    read = spy_outcome_reads(monkeypatch)
    orch = retime(
        orchestrate(
            engine,
            settings,
            pricing,
            agent_steps={"implement": AgentStep("B14", implement_input)},
            providers=FakeProviders(provider),  # type: ignore[arg-type]
        ),
        heartbeat_s=0.1,
    )

    with capture_logs() as logs:
        runner = asyncio.create_task(orch.run(task_id))
        try:
            await asyncio.wait_for(provider.entered.wait(), WAIT_S)
            await take_over_lease(engine, task_id)
            await settled(runner)
        finally:
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)

    assert runner.result().stopped == "lease_lost"
    after = await row(engine, task_id)
    assert (after.status, after.lease_owner) == ("running", "w-other")  # the new holder's, untouched
    seen = await kinds(engine, task_id)
    # No status_changed after the claim's and no step_finished: the run's own events are the runtime's.
    assert [(k, p) for k, p in seen if k.startswith("task.")] == [
        changed("queued", "running", None),
        started("implement"),
    ]
    assert [k for k, _ in seen if k.startswith("agent.")] == ["agent.run_started", "agent.run_finished"]
    stored = await runs(engine, task_id)
    assert [(r.status, r.error_code) for r in stored] == [("failed", "worker_cancelled")]
    assert logged(logs, "agent_step_run_unfinished") == []
    assert read == []  # a cancel is not an end the adapter reads back (F11)
    assert other_tasks() == []


# --- C2: the runtime closes its run before the engine's own write ---------------------------------------


@pytest.mark.parametrize("status", ["draft", "running"])
async def test_a_delivery_limit_closes_the_run_before_the_engine_finishes_the_step(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    pricing: PricingTable,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    """C2 (``CancelledError``): the limit cancels the step; the adapter lets the cancel through without
    reading the row, so the run is closed (``worker_cancelled``) before ``_finish`` writes the status."""
    settings = worker()
    queued = status == "running"
    task_id = await make_task("queued" if queued else "draft", None if queued else {"user_set": ["title"]})
    await ready_world(engine, settings)
    provider = SlowProvider(5.0)
    read = spy_outcome_reads(monkeypatch)
    step = "implement" if queued else "intake"
    agent = AgentStep("B14", implement_input) if queued else AgentStep("A02", intake_input)
    orch = retime(
        orchestrate(
            engine,
            settings,
            pricing,
            agent_steps={step: agent},
            providers=FakeProviders(provider),  # type: ignore[arg-type]
        ),
        heartbeat_s=0.1,
        delivery_limit_s=0.5,
    )

    with capture_logs() as logs:
        outcome = await asyncio.wait_for(orch.run(task_id), WAIT_S)

    assert (outcome.stopped, outcome.ran) == ("duration_exceeded", [step])
    stored = await runs(engine, task_id)
    assert [(r.status, r.error_code) for r in stored] == [("failed", "worker_cancelled")]
    after = await row(engine, task_id)
    seen = await events(engine, task_id)
    run_finished = next(seq for seq, kind, _ in seen if kind == "agent.run_finished")
    status_changed = next(
        seq for seq, kind, _ in seen if kind == "task.status_changed" and seq > run_finished
    )
    assert run_finished < status_changed
    pairs = [(kind, payload) for _, kind, payload in seen]
    if queued:
        assert (after.status, after.status_reason) == ("interrupted", "duration_exceeded")
        assert finished(step, "interrupted") in pairs
        assert pairs[-1] == changed("running", "interrupted", "duration_exceeded")
    else:
        assert (after.status, after.error_code) == ("failed", "duration_exceeded")
        assert finished(step, "failed") in pairs
        assert pairs[-1][0] == "task.status_changed" and pairs[-1][1]["to"] == "failed"
    assert lease_free(after)
    assert logged(logs, "agent_step_run_unfinished") == []
    assert read == []  # F11: no row is read for a cancel
    assert other_tasks() == []


# --- BC-24: lost acks of the run writer -----------------------------------------------------------------


def lose_ack_once(monkeypatch: pytest.MonkeyPatch, method: str) -> list[int]:
    """``AgentRunWriter.<method>`` does its work and then raises ``OSError``, once: the commit landed and
    the answer was lost. The list counts the calls."""
    original = getattr(AgentRunWriter, method)
    calls: list[int] = []

    async def lossy(self: AgentRunWriter, *args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        result = await original(self, *args, **kwargs)
        if len(calls) == 1:
            raise OSError("the acknowledgement was lost")
        return result

    monkeypatch.setattr(AgentRunWriter, method, lossy)
    return calls


async def test_a_lost_start_ack_leaves_one_failed_run_and_a_failed_task(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    pricing: PricingTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-24, N61: the start committed, its answer was lost; the run ends ``internal_error`` under its own
    row (one ``agent.run_started``, no id conflict) and the task fails ``internal_error``."""
    settings = worker()
    task_id = await make_task("queued")
    await ready_world(engine, settings)
    calls = lose_ack_once(monkeypatch, "start")
    orch = orchestrate(
        engine, settings, pricing, agent_steps={"implement": AgentStep("B14", implement_input)}
    )

    with capture_logs() as logs:
        outcome = await asyncio.wait_for(orch.run(task_id), WAIT_S)

    assert outcome.stopped == "failed"
    assert len(calls) >= 1
    stored = await runs(engine, task_id)
    assert [(r.status, r.error_code) for r in stored] == [("failed", "internal_error")]
    seen = await kinds(engine, task_id)
    assert [kind for kind, _ in seen].count("agent.run_started") == 1
    assert logged(logs, "agent_run_id_conflict") == []
    after = await row(engine, task_id)
    assert (after.status, after.error_code) == ("failed", "internal_error")
    assert lease_free(after)
    assert other_tasks() == []


async def test_a_lost_finish_ack_abandons_the_step_and_the_next_delivery_runs_a_new_one(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    pricing: PricingTable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC-24, N59: the finish committed, its answer was lost: the row says ``succeeded`` but the output is
    gone, so the step is abandoned (no ``step_finished``), and the second delivery uses a new run id and never
    reads the first one."""
    settings = worker()
    task_id = await make_task("draft", {"user_set": ["title"]})
    await ready_world(engine, settings)
    steps = {
        "intake": AgentStep("A02", intake_input),
        "plan": AgentStep("A03", plan_input, on_success=to_planned),
    }

    with monkeypatch.context() as patch:
        calls = lose_ack_once(patch, "finish")
        first = orchestrate(
            engine, settings, pricing, owner=delivery_owner(), agent_steps=steps, steps={"context": raw_done}
        )
        with capture_logs() as logs:
            outcome = await asyncio.wait_for(first.run(task_id), WAIT_S)
    assert len(calls) == 2  # the runtime retried the finish after the lost ack and found the row closed

    assert outcome.stopped == "abandoned"
    abandoned = logged(logs, "step_abandoned")
    assert [(e["step"], e["reason"]) for e in abandoned] == [("intake", "run_output_unread")]
    stored = await runs(engine, task_id)
    assert [(r.step, r.status) for r in stored] == [("intake", "succeeded")]
    first_id = stored[0].id
    assert finished("intake") not in await kinds(engine, task_id)
    after_first = await row(engine, task_id)
    assert lease_free(after_first)
    assert after_first.checkpoint["dispatch"]["planning_attempts"] == 1

    read = spy_outcome_reads(monkeypatch)
    second = orchestrate(
        engine, settings, pricing, owner=delivery_owner(), agent_steps=steps, steps={"context": raw_done}
    )
    outcome = await asyncio.wait_for(second.run(task_id), WAIT_S)

    assert outcome.stopped == "transitioned"
    stored = await runs(engine, task_id)
    assert [(r.step, r.status) for r in stored] == [
        ("intake", "succeeded"),
        ("intake", "succeeded"),
        ("plan", "succeeded"),
    ]
    assert len({r.id for r in stored}) == 3 and stored[1].id != first_id
    assert first_id not in read  # N59: the first run's row is never read for the second delivery
    after = await row(engine, task_id)
    assert after.status == "planned"
    assert after.checkpoint["dispatch"]["planning_attempts"] == 2  # one more than the first delivery's
    assert other_tasks() == []


# --- N53 (AG-13 C2): a step that does not stop must not keep the lease alive ----------------------------


@pytest.mark.parametrize("revoke", ["real", "control"])
@pytest.mark.parametrize("path", ["overrun", "give_up", "outer_cancel"])
async def test_a_step_that_does_not_stop_cannot_keep_the_lease_alive(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    pricing: PricingTable,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    revoke: str,
) -> None:
    """N53 with the real runtime: the engine revokes its owner (limit overrun, heartbeat give-up or an outer
    cancel); the runtime's own beat, which renews as the same owner, is refused from then on, so the lease
    lapses within one lease period and recovery takes the task. With ``revoke_lease_owner`` made a no-op (the
    control) the runtime's beat keeps the lease alive: the same scenario then shows more than 10 renewals and
    a lease that is still held after 6 s, so the real case proves something."""
    real = revoke == "real"
    settings = worker()
    task_id = await make_task("queued")
    await ready_world(engine, settings)
    owner = delivery_owner()
    provider = SwallowingProvider()
    orch = orchestrate(
        engine,
        settings,
        pricing,
        owner=owner,
        agent_steps={"implement": AgentStep("B14", implement_input)},
        providers=FakeProviders(provider),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(lease_module, "_LEASE", timedelta(seconds=2))  # after the build, before retime
    overrun = path == "overrun"
    extra = {"cleanup_grace_s": 0.3} if overrun else {}
    orch = retime(
        orch,
        heartbeat_s=0.1,
        lease_safety_s=0.5,
        delivery_limit_s=0.4 if overrun else 3600.0,
        **extra,
    )
    revocations = Revocations(monkeypatch, real=real)
    sessions = make_sessionmaker(engine)
    bus = RecordingBus(engine)

    with capture_logs() as logs, Renewals(engine) as renewals:
        runner = asyncio.create_task(orch.run(task_id))
        try:
            await asyncio.wait_for(provider.entered.wait(), WAIT_S)
            assert (await row(engine, task_id)).lease_owner == owner  # the engine's claim, kept by the beat
            if path == "give_up":

                async def refuses(*args: Any, **kwargs: Any) -> None:
                    raise OSError("the database is not reachable")

                # The engine heartbeat renews through renew_lease_flags, and _locked (in _finish) through
                # renew_lease: both are refused, as when the one name was patched.
                monkeypatch.setattr(engine_module, "renew_lease_flags", refuses)
                monkeypatch.setattr(engine_module, "renew_lease", refuses)
            elif path == "outer_cancel":
                await asyncio.sleep(0.3)
                runner.cancel()
            await until(lambda: bool(revocations.at), "the engine to revoke its owner")
            first = revocations.at[0]
            if real:
                while not await lapsed(engine, task_id):
                    assert time.monotonic() - first <= 2.5, "the lease did not lapse within 2.5 s"
                    await asyncio.sleep(0.05)
                assert time.monotonic() - first <= 2.5  # the check that ends the loop may be its first
                # Not one renewal since the revocation. Exact zero holds because both heartbeats open
                # bound_transaction (the connection is checked out) before renew_lease, so no await sits
                # between the revoke gate and before_cursor_execute: a renewal is either counted before
                # ``first`` or refused. A refactor that defers the checkout must allow gate-passed renewals.
                assert renewals.after(first) == []
                swept = await recover_expired(sessions, bus, AuditWriter(Redactor()))
                assert swept.recovered == 1
            else:
                await asyncio.sleep(max(first + 6.0 - time.monotonic(), 0))
                assert len(renewals.after(first)) > 10  # the runtime's beat goes on
                held = await row(engine, task_id)
                assert held.lease_owner == owner and not await lapsed(engine, task_id)
        finally:
            provider.release.set()  # the step ends only now: it swallowed every cancel
            await settled(runner)
        after_end = renewals.after(revocations.at[0]) if revocations.at else []

    cancelled = runner.cancelled()
    assert cancelled is (path == "outer_cancel")
    if not cancelled and real:
        assert runner.result().stopped == "lease_lost"
    after = await row(engine, task_id)
    stored = await runs(engine, task_id)
    if real:
        assert after_end == []  # nothing renewed afterwards either: the run's end renews nothing
        assert (after.status, after.status_reason, after.lease_owner) == (
            "interrupted",
            "lease_expired",
            None,
        )
        assert changed("running", "interrupted", "lease_expired") in await kinds(engine, task_id)
        assert [(r.status, r.error_code) for r in stored] == [("failed", "lease_lost")]
    else:
        assert len(after_end) > 10
    assert owner not in repr(logs)
    assert provider.swallowed >= 1 or not overrun  # the step was asked to stop and did not
    assert other_tasks() == []


# --- N54: the delivery waits for the run's close before it disposes the engine --------------------------


@pytest.fixture
def delivery_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker's environment and holders, bound without ``hold_*`` (those leak into other tests). No
    readiness report is recorded, so the gate is told it is not required (refused only in production)."""
    for key, value in make_test_env(LLM_REQUIRE_READINESS="false").items():
        monkeypatch.setenv(key, value)
    settings = load_settings()
    monkeypatch.setattr(registry_module, "_held", REGISTRY)
    monkeypatch.setattr(pricing_module, "_held", load_pricing(Path(settings.pricing_path)))


@pytest.mark.parametrize("drain", ["on", "off"])
async def test_a_delivery_drains_the_run_close_before_it_disposes_the_engine(
    engine: AsyncEngine,
    make_task,  # type: ignore[no-untyped-def]
    delivery_env: None,
    monkeypatch: pytest.MonkeyPatch,
    drain: str,
) -> None:
    """N54 (BC-30): ``execute_task`` reads its settings from the environment. The limit cancels the step
    while the run's finish is still writing (3 s); with the drain the finish ends first and the row is
    ``succeeded``; with no drain the loop's teardown cuts it, the row stays ``running`` and run recovery
    closes it ``interrupted``."""
    settings = load_settings()
    task_id = await make_task("draft", {"user_set": ["title"]})
    await ready_world(engine, settings, readiness=False)
    order: list[str] = []
    original_finish = AgentRunWriter.finish
    original_dispose = AsyncEngine.dispose
    real_build = jobs.build_orchestrator

    async def slow_finish(self: AgentRunWriter, *args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(3.0)
        result = await original_finish(self, *args, **kwargs)
        order.append("finished")
        return result

    async def dispose(self: AsyncEngine, *args: Any, **kwargs: Any) -> None:
        order.append("disposed")
        await original_dispose(self, *args, **kwargs)

    def build(**kwargs: Any) -> TaskEngine:
        orch = real_build(**kwargs, agent_steps={"intake": AgentStep("A02", intake_input)})
        return retime(orch, delivery_limit_s=1.0)

    monkeypatch.setattr(AgentRunWriter, "finish", slow_finish)
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    monkeypatch.setattr(jobs, "build_orchestrator", build)
    if drain == "off":
        monkeypatch.setattr(jobs, "RUNTIME_DRAIN_S", 0)

    def deliver() -> Any:
        return jobs.run_task.apply(args=[str(task_id)])

    with capture_logs() as logs:
        result = await asyncio.wait_for(asyncio.to_thread(deliver), 60)

    assert result.successful()
    assert result.get()["stopped"] == "duration_exceeded"
    after = await row(engine, task_id)
    assert (after.status, after.error_code) == ("failed", "duration_exceeded")
    stored = await runs(engine, task_id)
    abandoned = logged(logs, "delivery_tasks_abandoned")
    if drain == "on":
        assert [r.status for r in stored] == ["succeeded"]
        assert order.index("finished") < order.index("disposed")  # the close ended before the disposal
        assert abandoned == []
    else:
        assert len(abandoned) == 1
        assert [r.status for r in stored] == ["running"]
        assert "finished" not in order
        swept = await recover_runs(make_sessionmaker(engine), RecordingBus(), AuditWriter(Redactor()))
        assert swept.runs == 1
        # Run recovery closes it ``failed`` with the code ``interrupted`` (its own vocabulary).
        closed = await runs(engine, task_id)
        assert [(r.status, r.error_code) for r in closed] == [("failed", "interrupted")]
    assert other_tasks() == []
