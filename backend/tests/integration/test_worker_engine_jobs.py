"""Engine jobs (spec WP-3.2 §14 #39, rulings R-T5a..c): run_task, execute_task, dispatch_sweep."""

from __future__ import annotations

import pickle
import uuid
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.core.db import make_sessionmaker
from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Project
from app.models.tasks import Task
from app.worker import jobs
from app.worker.identity import worker_id
from app.workflow.dispatch import DispatchIncomplete, DispatchResult
from app.workflow.engine import EngineOutcome
from app.workflow.lease import ClaimResult
from tests.conftest import make_test_env, reset_data


@pytest.fixture(autouse=True)
def test_env(monkeypatch: pytest.MonkeyPatch, migrated_database: str) -> None:
    for key, value in make_test_env().items():
        monkeypatch.setenv(key, value)


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


def test_build_orchestrator_wires_the_ports() -> None:
    from app.core.config import load_settings
    from app.services.orchestrator import build_orchestrator

    settings = load_settings(make_test_env(MAX_ACTIVE_TASKS="5"))
    engine = build_orchestrator(
        sessions=object(),  # type: ignore[arg-type]
        bus=object(),  # type: ignore[arg-type]
        audit=object(),  # type: ignore[arg-type]
        settings=settings,
        worker_id="w1",
    )
    ports = engine._ports
    assert (ports.worker_id, ports.max_active, dict(ports.steps)) == ("w1", 5, {})
