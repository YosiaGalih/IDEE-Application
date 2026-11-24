"""Engine jobs (spec WP-3.2 §14 #39, rulings R-T5a..c): run_task, execute_task, dispatch_sweep."""

from __future__ import annotations

import asyncio
import gc
import logging
import pickle
import uuid
import weakref
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.agents.readiness import RUNTIMES_AVAILABLE
from app.agents.registry import AgentRegistry, load_registry
from app.agents.runtime import CLOSE_TIMEOUT_S
from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.core.security import hash_password
from app.llm.pricing import PricingTable, load_pricing
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.services import orchestrator as orchestrator_module
from app.services.agent_step import AgentStep
from app.worker import jobs
from app.worker import pricing as pricing_module
from app.worker import registry as registry_module
from app.worker.identity import worker_id
from app.workflow.dispatch import DispatchIncomplete, DispatchResult
from app.workflow.engine import EngineOutcome, LeaseGuard, LeaseLost, StepContext
from app.workflow.lease import ClaimResult
from tests.conftest import make_test_env, reset_data


@pytest.fixture(autouse=True)
def test_env(monkeypatch: pytest.MonkeyPatch, migrated_database: str) -> None:
    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def held(monkeypatch: pytest.MonkeyPatch, test_env: None) -> tuple[AgentRegistry, PricingTable]:
    """The registry and the pricing a worker holds, bound without ``hold_*`` (those leak into other tests)."""
    settings = load_settings()
    registry = load_registry(Path(settings.agent_registry_path))
    pricing = load_pricing(Path(settings.pricing_path))
    monkeypatch.setattr(registry_module, "_held", registry)
    monkeypatch.setattr(pricing_module, "_held", pricing)
    return registry, pricing


@pytest.fixture
async def draft_task(engine: AsyncEngine) -> uuid.UUID:
    await reset_data(engine)
    async with make_sessionmaker(engine)() as session:
        owner = User(email="owner@example.test", display_name="O", password_hash=hash_password("x" * 12))
        session.add(owner)
        await session.flush()
        project = Project(owner_id=owner.id, name="P", slug="p", workspace_ref="p")
        session.add(project)
        await session.flush()
        task = Task(project_id=project.id, title="t", prompt="p", workflow_type="create", created_by=owner.id)
        session.add(task)
        await session.commit()
        return task.id


async def row_of(engine: AsyncEngine, task_id: uuid.UUID) -> Any:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT status, error_code, lease_owner FROM tasks WHERE id = :id"), {"id": task_id}
        )
        return result.one()


class Spy:
    """Counts engine disposals and Redis closes, patched on the classes (both are slotted)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.disposed = 0
        self.closed = 0
        real_dispose = AsyncEngine.dispose
        real_aclose = Redis.aclose

        async def dispose(engine: AsyncEngine, *args: Any, **kwargs: Any) -> None:
            self.disposed += 1
            await real_dispose(engine, *args, **kwargs)

        async def aclose(redis: Redis, *args: Any, **kwargs: Any) -> None:
            self.closed += 1
            await real_aclose(redis, *args, **kwargs)

        monkeypatch.setattr(AsyncEngine, "dispose", dispose)
        monkeypatch.setattr(Redis, "aclose", aclose)


async def test_execute_task_fails_a_draft_with_no_registered_steps(
    engine: AsyncEngine, draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Spy(monkeypatch)
    outcome = await jobs.execute_task(draft_task)
    assert outcome.claim is ClaimResult.ACQUIRED
    assert outcome.stopped == "step_unavailable"
    row = await row_of(engine, draft_task)
    assert (row.status, row.error_code) == ("failed", "step_unavailable")
    assert (spy.disposed, spy.closed) == (1, 1)


async def test_each_delivery_gets_its_own_lease_owner(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    owners: list[str] = []

    class Noop:
        async def run(self, task_id: uuid.UUID) -> EngineOutcome:
            return EngineOutcome(ClaimResult.ACQUIRED, [], "done")

    def fake_build(**kwargs: Any) -> Noop:
        owners.append(kwargs["worker_id"])
        return Noop()

    monkeypatch.setattr(jobs, "build_orchestrator", fake_build)
    await jobs.execute_task(draft_task)
    await jobs.execute_task(draft_task)
    assert len(owners) == 2 and owners[0] != owners[1]
    assert all(o.startswith(f"{worker_id()}:") for o in owners)


async def test_resources_are_closed_when_the_engine_raises(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Spy(monkeypatch)

    class Boom:
        async def run(self, task_id: uuid.UUID) -> EngineOutcome:
            raise RuntimeError("engine blew up")

    monkeypatch.setattr(jobs, "build_orchestrator", lambda **_: Boom())
    with pytest.raises(RuntimeError, match="engine blew up"):
        await jobs.execute_task(draft_task)
    assert (spy.disposed, spy.closed) == (1, 1)


async def test_the_engine_is_disposed_when_the_redis_close_raises(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Spy(monkeypatch)

    async def broken_close(redis: Redis, *args: Any, **kwargs: Any) -> None:
        spy.closed += 1
        raise ConnectionError("redis gone")

    monkeypatch.setattr(Redis, "aclose", broken_close)

    class Noop:
        async def run(self, task_id: uuid.UUID) -> EngineOutcome:
            return EngineOutcome(ClaimResult.ACQUIRED, [], "done")

    monkeypatch.setattr(jobs, "build_orchestrator", lambda **_: Noop())
    with pytest.raises(ConnectionError):
        await jobs.execute_task(draft_task)
    assert (spy.disposed, spy.closed) == (1, 1)


def test_run_task_runs_the_engine_and_summarises(engine: AsyncEngine, draft_task: uuid.UUID) -> None:
    result = jobs.run_task.apply(args=[str(draft_task)]).get()
    assert result == {
        "task_id": str(draft_task),
        "claim": "acquired",
        "ran": [],
        "stopped": "step_unavailable",
    }


@pytest.mark.parametrize("claim", [ClaimResult.AT_CAPACITY, ClaimResult.BUSY])
def test_a_deferred_claim_returns_normally_and_is_logged(
    claim: ClaimResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = uuid.uuid4()

    class Deferred:
        async def run(self, tid: uuid.UUID) -> EngineOutcome:
            return EngineOutcome(claim)

    monkeypatch.setattr(jobs, "build_orchestrator", lambda **_: Deferred())
    with capture_logs() as logs:
        task = jobs.run_task.apply(args=[str(task_id)])
    assert task.successful()  # no retry, no raise: the dispatch sweep re-sends after the grace period
    assert task.get() == {"task_id": str(task_id), "claim": claim.value, "ran": [], "stopped": None}
    deferred = [e for e in logs if e["event"] == "task_claim_deferred"]
    assert len(deferred) == 1
    assert deferred[0]["task_id"] == str(task_id) and deferred[0]["reason"] == claim.value


def test_an_invalid_task_id_is_logged_and_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def never(**_: Any) -> None:
        raise AssertionError("the engine must not be built for a bad id")

    monkeypatch.setattr(jobs, "build_orchestrator", never)
    bad = "x" * 200
    with capture_logs() as logs:
        task = jobs.run_task.apply(args=[bad])
    assert task.successful()
    assert task.get() == {"status": "invalid_task_id"}
    entry = next(e for e in logs if e["event"] == "run_task_invalid_id")
    assert len(entry["value"]) <= 80


def test_engine_exceptions_propagate_to_celery_as_run_task_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Celery logs the failure itself; the raw message (it may carry a secret) must not reach that line."""
    secret = "sk-test-" + "engine-0123"
    task_id = uuid.uuid4()

    class Boom:
        async def run(self, task_id: uuid.UUID) -> EngineOutcome:
            raise RuntimeError("engine blew up with " + secret)

    monkeypatch.setattr(jobs, "build_orchestrator", lambda **_: Boom())
    with capture_logs() as logs:
        task = jobs.run_task.apply(args=[str(task_id)])
    assert task.failed()
    assert isinstance(task.result, jobs.RunTaskFailed)
    assert str(task.result) == "RuntimeError"
    assert secret not in str(task.result) and secret not in task.traceback
    assert task.result.__suppress_context__  # `from None`: no chained traceback with the original text
    failed = [e for e in logs if e["event"] == "run_task_failed"]
    assert [(e["log_level"], e["task_id"], e["error_type"]) for e in failed] == [
        ("error", str(task_id), "RuntimeError")
    ]
    assert secret not in repr(logs) and "exc_info" not in failed[0]


def test_a_failure_before_any_step_is_logged_by_run_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup or claim failures (a database outage) never reach the engine's own log: run_task covers them."""
    task_id = uuid.uuid4()
    secret = "sk-test-" + "setup-0123"

    def broken(**_: Any) -> None:
        raise OSError("database down " + secret)

    monkeypatch.setattr(jobs, "build_orchestrator", broken)
    with capture_logs() as logs:
        task = jobs.run_task.apply(args=[str(task_id)])
    assert task.failed() and str(task.result) == "OSError"
    assert [(e["task_id"], e["error_type"]) for e in logs if e["event"] == "run_task_failed"] == [
        (str(task_id), "OSError")
    ]
    assert [e for e in logs if e["event"] == "task_engine_failed"] == []
    assert secret not in repr(logs) and secret not in task.traceback


def test_run_task_failed_survives_pickling() -> None:
    again = pickle.loads(pickle.dumps(jobs.RunTaskFailed("OSError")))  # noqa: S301 - our own exception, in-process
    assert isinstance(again, jobs.RunTaskFailed)
    assert (str(again), again.error_type) == ("OSError", "OSError")


@pytest.mark.parametrize(
    ("claim", "event", "level"),
    [
        (ClaimResult.NOT_FOUND, "task_not_found", "warning"),
        (ClaimResult.HELD, "task_claim_skipped", "info"),
        (ClaimResult.NOT_CLAIMABLE, "task_claim_skipped", "info"),
    ],
)
def test_a_skipped_claim_is_logged(
    claim: ClaimResult, event: str, level: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = uuid.uuid4()

    class Skipped:
        async def run(self, tid: uuid.UUID) -> EngineOutcome:
            return EngineOutcome(claim)

    monkeypatch.setattr(jobs, "build_orchestrator", lambda **_: Skipped())
    with capture_logs() as logs:
        task = jobs.run_task.apply(args=[str(task_id)])
    assert task.successful()
    assert task.get() == {"task_id": str(task_id), "claim": claim.value, "ran": [], "stopped": None}
    entries = [e for e in logs if e["event"] in ("task_not_found", "task_claim_skipped")]
    assert [(e["event"], e["log_level"], e["task_id"], e["reason"]) for e in entries] == [
        (event, level, str(task_id), claim.value)
    ]


class FakeDispatcher:
    def __init__(self) -> None:
        self.sent: list[uuid.UUID] = []

    async def enqueue_task(self, task_id: uuid.UUID) -> None:
        self.sent.append(task_id)


async def test_dispatch_sweep_redispatches_through_the_injected_dispatcher(
    engine: AsyncEngine, draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE tasks SET checkpoint = jsonb_build_object('dispatch', jsonb_build_object("
                "'at', to_char(now() - interval '10 minutes', 'YYYY-MM-DD\"T\"HH24:MI:SSOF'))) WHERE id = :id"
            ),
            {"id": draft_task},
        )
    spy = Spy(monkeypatch)
    dispatcher = FakeDispatcher()
    result = await jobs.sweep_dispatch(dispatcher=dispatcher)
    assert result == DispatchResult(candidates=1, dispatched=1, failed=0, skipped=0)
    assert dispatcher.sent == [draft_task]
    assert spy.disposed == 1


def test_dispatch_sweep_job_returns_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(*, dispatcher: Any = None) -> DispatchResult:
        return DispatchResult(candidates=3, dispatched=3, failed=0, skipped=0)

    monkeypatch.setattr(jobs, "sweep_dispatch", fake)
    task = jobs.dispatch_sweep.apply()
    assert task.successful()
    assert task.get() == {"candidates": 3, "dispatched": 3, "failed": 0, "skipped": 0}


def test_dispatch_sweep_job_fails_when_an_enqueue_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-F3: like recovery_sweep, a sweep that left tasks undispatched must not end as a success."""

    async def fake(*, dispatcher: Any = None) -> DispatchResult:
        return DispatchResult(candidates=3, dispatched=2, failed=1, skipped=0)

    monkeypatch.setattr(jobs, "sweep_dispatch", fake)
    task = jobs.dispatch_sweep.apply()
    assert task.failed()
    assert isinstance(task.result, DispatchIncomplete)
    assert task.result.result == DispatchResult(candidates=3, dispatched=2, failed=1, skipped=0)


def test_dispatch_incomplete_survives_pickling() -> None:
    result = DispatchResult(candidates=3, dispatched=2, failed=1, skipped=4)
    again = pickle.loads(pickle.dumps(DispatchIncomplete(result)))  # noqa: S301 - our own exception, in-process
    assert isinstance(again, DispatchIncomplete)
    assert again.result == result and str(again) == "1 of 3 orphaned dispatches were not sent"


async def test_dispatch_sweep_defaults_to_the_celery_job_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[str] = []

    class Recorder(FakeDispatcher):
        def __init__(self, broker_url: str) -> None:
            super().__init__()
            built.append(broker_url)

    monkeypatch.setattr(jobs, "CeleryJobQueue", Recorder)
    await jobs.sweep_dispatch()
    assert built == [make_test_env()["REDIS_URL"]]


def test_build_orchestrator_wires_the_ports(
    held: tuple[AgentRegistry, PricingTable], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.orchestrator import build_orchestrator
    from app.workflow.lease import LEASE_SAFETY_SECONDS

    def never(**_: Any) -> None:
        raise AssertionError("no agent step is registered: no runtime is built (BC-2)")

    monkeypatch.setattr(orchestrator_module, "build_agent_runtime", never)
    settings = load_settings(make_test_env(MAX_ACTIVE_TASKS="5", TASK_DELIVERY_MAX_MINUTES="45"))
    engine = build_orchestrator(
        sessions=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
        audit=object(),  # type: ignore[arg-type]
        settings=settings,
        worker_id="w1",
        registry=held[0],
        pricing=held[1],
    )
    ports = engine._ports
    assert (ports.worker_id, ports.max_active, dict(ports.steps)) == ("w1", 5, {})
    assert (ports.delivery_limit_s, ports.lease_safety_s) == (45 * 60, LEASE_SAFETY_SECONDS)


async def test_dispatch_sweep_forwards_max_active(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake(sessions: Any, dispatcher: Any, **kwargs: Any) -> DispatchResult:
        seen.update(kwargs)
        return DispatchResult(0, 0, 0, 0)

    monkeypatch.setattr(jobs, "redispatch_orphans", fake)
    Spy(monkeypatch)
    await jobs.sweep_dispatch(dispatcher=FakeDispatcher())
    from app.core.config import load_settings

    assert seen == {"max_active": load_settings().max_active_tasks}


class EmptyInput(BaseModel):
    pass


async def build_input(ctx: StepContext) -> BaseModel:
    return EmptyInput()


def plain_step_ctx() -> StepContext:
    task_id = uuid.uuid4()
    return StepContext(
        task_id=task_id,
        project_id=uuid.uuid4(),
        step="implement",
        checkpoint={},
        lease=LeaseGuard(task_id, "w-c2"),
        pipeline="running",
    )


async def test_the_runtime_gets_the_same_owner_as_the_engine_ports(
    held: tuple[AgentRegistry, PricingTable], monkeypatch: pytest.MonkeyPatch
) -> None:
    """C2 (AG-13): the heartbeat of the runtime renews the lease of the owner the engine claimed with."""
    registry, pricing = held
    calls: list[dict[str, Any]] = []
    seen: list[Any] = []

    class Sentinel:
        async def run(self, request: Any) -> Any:
            seen.append(request)
            raise LeaseLost  # re-raised by the adapter: the test needs no result

    class FakeReads:
        def __init__(self, sessions: Any) -> None:
            self.sessions = sessions

        async def profile_exists(self, agent_id: str) -> bool:
            return True

    def spy(**kwargs: Any) -> Sentinel:
        calls.append(kwargs)
        return Sentinel()

    monkeypatch.setattr(orchestrator_module, "build_agent_runtime", spy)
    monkeypatch.setattr(orchestrator_module, "DbAgentStepReads", FakeReads)
    sessions, bus, audit = object(), object(), object()
    engine = orchestrator_module.build_orchestrator(
        sessions=sessions,  # type: ignore[arg-type]
        bus=bus,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        settings=load_settings(),
        worker_id="w-c2",
        registry=registry,
        pricing=pricing,
        agent_steps={"implement": AgentStep("B14", build_input)},
    )
    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["worker_id"] == engine._ports.worker_id == "w-c2"
    assert kwargs["registry"] is registry and kwargs["pricing"] is pricing
    assert kwargs["audit"] is audit and kwargs["bus"] is bus and kwargs["sessions"] is sessions
    assert kwargs["providers"] is None and kwargs["tools"] is None
    with pytest.raises(LeaseLost):
        await engine._ports.steps["implement"](plain_step_ctx())
    assert len(seen) == 1 and seen[0].agent_id == "B14"


def test_a_step_name_in_both_registries_is_refused(held: tuple[AgentRegistry, PricingTable]) -> None:
    async def plain(ctx: StepContext) -> Any:
        raise AssertionError("never run")

    with pytest.raises(ValueError, match="implement"):
        orchestrator_module.build_orchestrator(
            sessions=object(),  # type: ignore[arg-type]
            bus=object(),  # type: ignore[arg-type]
            audit=object(),  # type: ignore[arg-type]
            settings=load_settings(),
            worker_id="w1",
            registry=held[0],
            pricing=held[1],
            steps={"implement": plain},
            agent_steps={"implement": AgentStep("B14", build_input)},
        )


async def test_a_wrong_registration_fails_before_the_claim(
    engine: AsyncEngine, draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = Spy(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "AGENT_STEPS", {"plan": AgentStep("A02", build_input)})
    with pytest.raises(ValueError, match="plan"):
        await jobs.execute_task(draft_task)
    row = await row_of(engine, draft_task)
    assert (row.status, row.lease_owner) == ("draft", None)
    assert (spy.disposed, spy.closed) == (1, 1)


def test_a_wrong_registration_is_refused_before_the_runtime_is_built(
    held: tuple[AgentRegistry, PricingTable], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check is pure; the runtime build reads the prompt book from disk, so it must come after."""
    built: list[object] = []

    def spy(**kwargs: Any) -> None:
        built.append(kwargs)
        raise AssertionError("the runtime must not be built for a wrong registration")

    monkeypatch.setattr(orchestrator_module, "build_agent_runtime", spy)
    with pytest.raises(ValueError, match="cannot run agent"):
        orchestrator_module.build_orchestrator(
            sessions=None,  # type: ignore[arg-type]
            bus=None,  # type: ignore[arg-type]
            audit=None,  # type: ignore[arg-type]
            settings=load_settings(),
            worker_id="w1",
            registry=held[0],
            pricing=held[1],
            agent_steps={"plan": AgentStep("A02", build_input)},
        )
    assert built == []


def test_no_agent_step_is_registered_and_the_engine_stays_off() -> None:
    """N27: a later work package registers the steps and flips the flag."""
    assert dict(orchestrator_module.AGENT_STEPS) == {}
    assert RUNTIMES_AVAILABLE["workflow_engine"] is False


async def test_the_held_registry_and_pricing_are_injected(
    draft_task: uuid.UUID, held: tuple[AgentRegistry, PricingTable], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    class Noop:
        async def run(self, task_id: uuid.UUID) -> EngineOutcome:
            return EngineOutcome(ClaimResult.ACQUIRED, [], "done")

    def fake_build(**kwargs: Any) -> Noop:
        seen.update(kwargs)
        return Noop()

    monkeypatch.setattr(jobs, "build_orchestrator", fake_build)
    await jobs.execute_task(draft_task)
    assert seen["registry"] is held[0] and seen["pricing"] is held[1]


@pytest.mark.parametrize(
    ("module", "error"), [(registry_module, "RegistryNotLoaded"), (pricing_module, "PricingNotLoaded")]
)
async def test_an_unloaded_holder_stops_before_any_connection_or_claim(
    engine: AsyncEngine,
    draft_task: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    error: str,
) -> None:
    def never(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("nothing may be built or opened")

    monkeypatch.setattr(module, "_held", None)
    monkeypatch.setattr(jobs, "make_engine", never)
    monkeypatch.setattr(jobs, "build_orchestrator", never)
    with pytest.raises(RuntimeError) as caught:
        await jobs.execute_task(draft_task)
    assert type(caught.value).__name__ == error
    row = await row_of(engine, draft_task)
    assert (row.status, row.lease_owner) == ("draft", None)


@pytest.mark.parametrize(
    ("module", "error"), [(registry_module, "RegistryNotLoaded"), (pricing_module, "PricingNotLoaded")]
)
def test_an_unloaded_holder_fails_run_task_by_type(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch, module: Any, error: str
) -> None:
    monkeypatch.setattr(module, "_held", None)
    with capture_logs() as logs:
        task = jobs.run_task.apply(args=[str(draft_task)])
    assert task.failed() and isinstance(task.result, jobs.RunTaskFailed)
    assert str(task.result) == error
    assert [(e["task_id"], e["error_type"]) for e in logs if e["event"] == "run_task_failed"] == [
        (str(draft_task), error)
    ]


class Order:
    """The order of the end of a delivery: what a leftover task records, and the engine disposal."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.events: list[str] = []
        real_dispose = AsyncEngine.dispose

        async def dispose(engine: AsyncEngine, *args: Any, **kwargs: Any) -> None:
            self.events.append("disposed")
            await real_dispose(engine, *args, **kwargs)

        monkeypatch.setattr(AsyncEngine, "dispose", dispose)


class Leftovers:
    """Tasks a fake engine leaves behind; cancelled in teardown so none leaks into another test."""

    def __init__(self) -> None:
        self.tasks: list[asyncio.Future[None]] = []
        self.weak: list[weakref.ref[asyncio.Future[None]]] = []

    def spawn(self, order: Order, seconds: float) -> None:
        async def slow() -> None:
            await asyncio.sleep(seconds)
            order.events.append("closed")

        self.tasks.append(asyncio.ensure_future(slow()))

    def spawn_failing(self, seconds: float, message: str) -> None:
        """A leftover that fails; the loop alone holds it, as for a run's close that nobody awaits."""

        async def close_fails() -> None:
            await asyncio.sleep(seconds)
            raise RuntimeError(message)

        self.weak.append(weakref.ref(asyncio.ensure_future(close_fails())))  # held by the loop alone


@pytest.fixture
async def leftovers() -> Any:
    holder = Leftovers()
    yield holder
    alive = [t for t in holder.tasks + [r() for r in holder.weak] if t is not None]
    for t in alive:
        t.cancel()
    await asyncio.gather(*alive, return_exceptions=True)


def leaving(order: Order, leftovers: Leftovers, seconds: float, *, raises: bool = False) -> Any:
    class Leaves:
        async def run(self, task_id: uuid.UUID) -> EngineOutcome:
            leftovers.spawn(order, seconds)
            if raises:
                raise RuntimeError("engine blew up")
            return EngineOutcome(ClaimResult.ACQUIRED, [], "done")

    return lambda **_: Leaves()


async def test_a_delivery_waits_for_its_tasks_before_the_disposal(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch, leftovers: Leftovers
) -> None:
    order = Order(monkeypatch)
    monkeypatch.setattr(jobs, "build_orchestrator", leaving(order, leftovers, 0.2))
    with capture_logs() as logs:
        await jobs.execute_task(draft_task)
    assert order.events == ["closed", "disposed"]
    assert [e for e in logs if e["event"] == "delivery_tasks_abandoned"] == []


async def test_a_task_that_outlives_the_drain_is_logged_and_never_raises(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch, leftovers: Leftovers
) -> None:
    order = Order(monkeypatch)
    spy = Spy(monkeypatch)
    monkeypatch.setattr(jobs, "RUNTIME_DRAIN_S", 0.05)
    monkeypatch.setattr(jobs, "build_orchestrator", leaving(order, leftovers, 1.0))
    with capture_logs() as logs:
        await jobs.execute_task(draft_task)
    abandoned = [e for e in logs if e["event"] == "delivery_tasks_abandoned"]
    assert [(e["log_level"], e["task_id"], e["count"]) for e in abandoned] == [
        ("warning", str(draft_task), 1)
    ]
    assert order.events == ["disposed"] and (spy.disposed, spy.closed) == (1, 1)


async def test_a_task_of_the_test_is_not_waited_for(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only tasks created during the delivery are drained, not the ones that were already running."""
    outsider = asyncio.ensure_future(asyncio.sleep(5))
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        with capture_logs() as logs:
            await jobs.execute_task(draft_task)
        assert loop.time() - started < 1.0
        assert [e for e in logs if e["event"] == "delivery_tasks_abandoned"] == []
    finally:
        outsider.cancel()
        await asyncio.gather(outsider, return_exceptions=True)


async def test_the_drain_runs_when_the_engine_raises(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch, leftovers: Leftovers
) -> None:
    order = Order(monkeypatch)
    monkeypatch.setattr(jobs, "build_orchestrator", leaving(order, leftovers, 0.2, raises=True))
    with pytest.raises(RuntimeError, match="engine blew up"):
        await jobs.execute_task(draft_task)
    assert order.events == ["closed", "disposed"]


def test_the_drain_covers_the_three_bounded_writes_of_a_run_end() -> None:
    assert jobs.RUNTIME_DRAIN_S == 3 * CLOSE_TIMEOUT_S == 15.0


LEAK = "sk-test-" + "leak-9876"


async def test_a_failed_leftover_is_logged_by_type_and_never_left_unretrieved(
    draft_task: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    leftovers: Leftovers,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Leaves:
        async def run(self, task_id: uuid.UUID) -> EngineOutcome:
            leftovers.spawn_failing(0.05, LEAK)
            return EngineOutcome(ClaimResult.ACQUIRED, [], "done")

    monkeypatch.setattr(jobs, "build_orchestrator", lambda **_: Leaves())
    with caplog.at_level(logging.ERROR, logger="asyncio"), capture_logs() as logs:
        await jobs.execute_task(draft_task)
        gc.collect()
    failed = [e for e in logs if e["event"] == "delivery_task_failed"]
    assert [(e["log_level"], e["task_id"], e["error_type"]) for e in failed] == [
        ("warning", str(draft_task), "RuntimeError")
    ]
    assert failed[0]["task"].endswith("close_fails")
    assert LEAK not in repr(logs) and all("exc_info" not in e for e in logs)
    assert LEAK not in caplog.text and "never retrieved" not in caplog.text


async def test_the_abandoned_log_names_the_pending_tasks(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch, leftovers: Leftovers
) -> None:
    order = Order(monkeypatch)
    monkeypatch.setattr(jobs, "RUNTIME_DRAIN_S", 0.05)
    monkeypatch.setattr(jobs, "build_orchestrator", leaving(order, leftovers, 1.0))
    with capture_logs() as logs:
        await jobs.execute_task(draft_task)
    abandoned = [e for e in logs if e["event"] == "delivery_tasks_abandoned"]
    assert [e["tasks"] for e in abandoned] == [[leftovers.spawn.__qualname__ + ".<locals>.slow"]]


async def test_the_delivery_owner_is_the_worker_id_of_the_engine(
    draft_task: uuid.UUID, monkeypatch: pytest.MonkeyPatch, test_env: None
) -> None:
    seen: dict[str, Any] = {}

    class Noop:
        async def run(self, task_id: uuid.UUID) -> EngineOutcome:
            return EngineOutcome(ClaimResult.ACQUIRED, [], "done")

    def fake_build(**kwargs: Any) -> Noop:
        seen.update(kwargs)
        return Noop()

    monkeypatch.setattr(jobs, "delivery_owner", lambda: "owner-fixed-for-the-test")
    monkeypatch.setattr(jobs, "build_orchestrator", fake_build)
    await jobs.execute_task(draft_task)
    assert seen["worker_id"] == "owner-fixed-for-the-test"
