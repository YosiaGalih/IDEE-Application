"""The AgentStep adapter without a database (WP-3.2c2c2 Task 2; spec §16.6.5 B, N56-N59, N63)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel
from structlog.testing import capture_logs

from app.agents.grants import AgentNotRunnable
from app.agents.runtime import (
    AgentInputInvalid,
    AgentInputTooLarge,
    AgentRunIdConflict,
    AgentRunRequest,
    AgentRunResult,
    AgentRunUnfinished,
    ScenarioNotAllowed,
)
from app.core.cancel import CancelToken
from app.services.agent_runtime import RunOutcome
from app.services.agent_step import AgentStep, AgentSuccess, bind_agent_steps, done
from app.workflow import engine, lease, states
from app.workflow.engine import Pipeline, StepAbandoned, StepContext, StepResult
from app.workflow.lease import LeaseGuard, LeaseLost
from app.workflow.run_outcomes import FAIL_MESSAGES
from tests.runtime_fakes import REGISTRY, registry_with

SENTINEL = "sk-test-" + "leak-0123"
TASK_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
USAGE: Any = None  # the adapter never reads the usage


class Payload(BaseModel):
    task_id: uuid.UUID
    note: str = "n"


def make_ctx(
    step: str = "plan", pipeline: Pipeline = "draft", checkpoint: dict[str, Any] | None = None
) -> StepContext:
    return StepContext(
        task_id=TASK_ID,
        project_id=PROJECT_ID,
        step=step,
        checkpoint=checkpoint or {},
        lease=LeaseGuard(TASK_ID, "w-unit"),
        pipeline=pipeline,
    )


def make_result(
    run_id: uuid.UUID, status: str = "succeeded", code: str | None = None, **kw: Any
) -> AgentRunResult:
    return AgentRunResult(
        agent_run_id=run_id,
        status=status,  # type: ignore[arg-type]
        output=kw.get("output", Payload(task_id=TASK_ID, note=SENTINEL)),
        usage=USAGE,
        error_code=code,  # type: ignore[arg-type]
        error=kw.get("error", SENTINEL),
        budget=None,
        pending_tool=None,
    )


class FakeRuntime:
    """Records requests; ``script`` returns an AgentRunResult or raises."""

    def __init__(self, script: Callable[[AgentRunRequest], AgentRunResult | BaseException]) -> None:
        self.script = script
        self.requests: list[AgentRunRequest] = []

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        outcome = self.script(request)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeReads:
    def __init__(self, *, exists: bool = True, outcome: RunOutcome | None = None) -> None:
        self.exists = exists
        self.outcome = outcome or RunOutcome("missing", None, None)
        self.profile_calls: list[str] = []
        self.outcome_calls: list[tuple[uuid.UUID, uuid.UUID, str]] = []

    async def profile_exists(self, agent_id: str) -> bool:
        self.profile_calls.append(agent_id)
        return self.exists

    async def run_outcome(self, agent_run_id: uuid.UUID, *, task_id: uuid.UUID, agent_id: str) -> RunOutcome:
        self.outcome_calls.append((agent_run_id, task_id, agent_id))
        return self.outcome


async def build_payload(ctx: StepContext) -> BaseModel:
    return Payload(task_id=ctx.task_id)


def bound(
    runtime: FakeRuntime,
    reads: FakeReads,
    *,
    step: str = "plan",
    agent_id: str = "A03",
    on_success: Any = done,
    build_input: Any = build_payload,
) -> Callable[[StepContext], Any]:
    steps = bind_agent_steps(
        {step: AgentStep(agent_id, build_input, on_success)}, runtime=runtime, reads=reads, registry=REGISTRY
    )
    return steps[step]


def ok(request: AgentRunRequest) -> AgentRunResult:
    return make_result(request.agent_run_id)


# --- catch order (N57) ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError(),
        AgentRunIdConflict(SENTINEL),
        LeaseLost(SENTINEL),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_propagated_errors_never_read_the_row(error: BaseException) -> None:
    runtime, reads = FakeRuntime(lambda _r: error), FakeReads()
    with pytest.raises(type(error)):
        await bound(runtime, reads)(make_ctx())
    assert reads.outcome_calls == []


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AgentNotRunnable(SENTINEL), "agent_unavailable"),
        (AgentInputInvalid(SENTINEL), "agent_input_invalid"),
        (AgentInputTooLarge(SENTINEL), "agent_input_invalid"),
        (ScenarioNotAllowed(SENTINEL), "agent_input_invalid"),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else v,
)
async def test_refusals_fail_with_a_fixed_sentence(error: BaseException, code: str) -> None:
    runtime, reads = FakeRuntime(lambda _r: error), FakeReads()
    with capture_logs() as logs:
        result = await bound(runtime, reads)(make_ctx())
    assert (result.kind, result.error_code) == ("fail", code)
    assert result.message == FAIL_MESSAGES[code].replace("{agent_id}", "A03")
    assert reads.outcome_calls == []
    assert SENTINEL not in repr(logs)
    assert [log["event"] for log in logs].count("agent_step_refused") == 1
    assert all("exc_info" not in log for log in logs)


@pytest.mark.parametrize(
    "error",
    [
        AgentRunUnfinished("x", agent_run_id=uuid.uuid4(), task_id=TASK_ID),
        RuntimeError(SENTINEL),
        TimeoutError(),
        OSError(SENTINEL),
    ],
    ids=lambda e: type(e).__name__,
)
async def test_other_errors_read_the_row_once_and_map_it(error: Exception) -> None:
    runtime = FakeRuntime(lambda _r: error)
    reads = FakeReads(outcome=RunOutcome("finished", "failed", "timeout"))
    with capture_logs() as logs:
        result = await bound(runtime, reads)(make_ctx())
    assert len(reads.outcome_calls) == 1
    assert reads.outcome_calls[0] == (runtime.requests[0].agent_run_id, TASK_ID, "A03")
    assert (result.kind, result.error_code) == ("fail", "timeout")
    assert SENTINEL not in repr(logs)
    unfinished = [log for log in logs if log["event"] == "agent_step_run_unfinished"]
    assert len(unfinished) == 1 and unfinished[0]["error_type"] == type(error).__name__
    assert all("exc_info" not in log for log in logs)


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (RunOutcome("missing", None, None), "run_outcome_missing"),
        (RunOutcome("running", "running", None), "run_outcome_running"),
        (RunOutcome("finished", "succeeded", None), "run_output_unread"),
        (RunOutcome("finished", "failed", "worker_cancelled"), "run_worker_cancelled"),
    ],
)
async def test_row_abandons(outcome: RunOutcome, reason: str) -> None:
    runtime, reads = FakeRuntime(lambda _r: RuntimeError()), FakeReads(outcome=outcome)
    with pytest.raises(StepAbandoned) as info:
        await bound(runtime, reads)(make_ctx())
    assert info.value.reason == reason


async def test_row_lease_lost_is_raised() -> None:
    runtime = FakeRuntime(lambda _r: RuntimeError())
    reads = FakeReads(outcome=RunOutcome("finished", "failed", "lease_lost"))
    with pytest.raises(LeaseLost):
        await bound(runtime, reads)(make_ctx())


# --- returned results -------------------------------------------------------------------------------------


async def test_succeeded_hands_the_success_to_on_success() -> None:
    seen: list[tuple[StepContext, AgentSuccess]] = []

    async def on_success(ctx: StepContext, success: AgentSuccess) -> StepResult:
        seen.append((ctx, success))
        return StepResult.done()

    runtime, reads = FakeRuntime(ok), FakeReads()
    ctx = make_ctx()
    result = await bound(runtime, reads, on_success=on_success)(ctx)
    assert result.kind == "done"
    ((got_ctx, success),) = seen
    assert got_ctx is ctx
    assert success.agent_run_id == runtime.requests[0].agent_run_id
    assert success.agent_id == "A03"
    assert isinstance(success.output, Payload)
    assert "output" not in repr(success)
    assert reads.outcome_calls == []


async def test_default_on_success_is_done() -> None:
    result = await bound(FakeRuntime(ok), FakeReads())(make_ctx())
    assert result.kind == "done"


@pytest.mark.parametrize(
    ("output", "code"),
    [(None, None), (Payload(task_id=TASK_ID), "model_error")],
    ids=["no_output", "error_code"],
)
async def test_a_succeeded_result_without_a_clean_output_abandons(output: Any, code: str | None) -> None:
    handed: list[AgentSuccess] = []

    async def on_success(ctx: StepContext, success: AgentSuccess) -> StepResult:
        handed.append(success)
        return StepResult.done()

    runtime = FakeRuntime(lambda r: make_result(r.agent_run_id, "succeeded", code, output=output))
    with capture_logs() as logs, pytest.raises(StepAbandoned) as info:
        await bound(runtime, FakeReads(), on_success=on_success)(make_ctx())
    assert info.value.reason == "run_outcome_unknown"
    assert handed == []  # evidence over claims: no success without an output
    assert [log["log_level"] for log in logs if log["event"] == "run_outcome_unknown"] == ["error"]


async def test_an_on_success_error_propagates_without_a_row_read() -> None:
    async def on_success(ctx: StepContext, success: AgentSuccess) -> StepResult:
        raise RuntimeError("step bug")

    runtime, reads = FakeRuntime(ok), FakeReads()
    with pytest.raises(RuntimeError, match="step bug"):
        await bound(runtime, reads, on_success=on_success)(make_ctx())
    assert reads.outcome_calls == []


@pytest.mark.parametrize(("pipeline", "kind"), [("draft", "fail"), ("running", "interrupt")])
async def test_provider_stop_follows_the_pipeline(pipeline: Pipeline, kind: str) -> None:
    runtime = FakeRuntime(lambda r: make_result(r.agent_run_id, "failed", "provider_not_ready", output=None))
    result = await bound(runtime, FakeReads(), step="implement", agent_id="B14")(
        make_ctx("implement", pipeline)
    )
    assert result.kind == kind
    assert (result.error_code or result.reason) == "provider_not_ready"


async def test_failed_lease_lost_raises() -> None:
    runtime = FakeRuntime(lambda r: make_result(r.agent_run_id, "failed", "lease_lost", output=None))
    with pytest.raises(LeaseLost):
        await bound(runtime, FakeReads())(make_ctx())


async def test_cancelled_abandons() -> None:
    runtime = FakeRuntime(lambda r: make_result(r.agent_run_id, "cancelled", None, output=None))
    with pytest.raises(StepAbandoned) as info:
        await bound(runtime, FakeReads())(make_ctx())
    assert info.value.reason == "run_cancelled"


async def test_approval_required_fails() -> None:
    runtime = FakeRuntime(lambda r: make_result(r.agent_run_id, "approval_required", None, output=None))
    result = await bound(runtime, FakeReads())(make_ctx())
    assert (result.kind, result.error_code) == ("fail", "approval_unsupported")
    assert result.message == FAIL_MESSAGES["approval_unsupported"].replace("{agent_id}", "A03")


async def test_fail_message_never_carries_the_run_text() -> None:
    runtime = FakeRuntime(lambda r: make_result(r.agent_run_id, "failed", "model_error", output=None))
    with capture_logs() as logs:
        result = await bound(runtime, FakeReads())(make_ctx())
    assert result.message == FAIL_MESSAGES["model_error"].replace("{agent_id}", "A03")
    assert SENTINEL not in result.message and SENTINEL not in repr(logs)
    ended = [log for log in logs if log["event"] == "agent_step_run_ended"]
    assert len(ended) == 1
    assert ended[0]["source"] == "result" and ended[0]["verdict"] == "fail"
    assert ended[0]["run_status"] == "failed" and ended[0]["run_error_code"] == "model_error"
    assert all("exc_info" not in log for log in logs)


async def test_unknown_outcome_logs_an_error_then_abandons() -> None:
    runtime = FakeRuntime(lambda _r: RuntimeError(SENTINEL))
    reads = FakeReads(outcome=RunOutcome("weird", "x", None))  # type: ignore[arg-type]
    with capture_logs() as logs, pytest.raises(StepAbandoned) as info:
        await bound(runtime, reads)(make_ctx())
    assert info.value.reason == "run_outcome_unknown"
    assert [log["event"] for log in logs].count("run_outcome_unknown") == 1
    assert [log["log_level"] for log in logs if log["event"] == "run_outcome_unknown"] == ["error"]
    assert SENTINEL not in repr(logs)


# --- fresh id (N59), N56, BC-9, request ---------------------------------------------------------------------


async def test_every_call_uses_a_new_version_4_id() -> None:
    fixed = uuid.uuid4()
    runtime = FakeRuntime(ok)
    step = bound(runtime, FakeReads())
    await step(make_ctx(checkpoint={"agent_run_id": str(fixed)}))
    await step(make_ctx())
    first, second = (r.agent_run_id for r in runtime.requests)
    assert first != second and fixed not in (first, second)
    assert first.version == second.version == 4


async def test_the_id_read_on_error_is_the_requests() -> None:
    runtime, reads = FakeRuntime(lambda _r: OSError()), FakeReads()
    with pytest.raises(StepAbandoned):
        await bound(runtime, reads)(make_ctx())
    assert reads.outcome_calls[0][0] == runtime.requests[0].agent_run_id


async def test_missing_profile_abandons_before_any_run() -> None:
    runtime, reads = FakeRuntime(ok), FakeReads(exists=False)
    with capture_logs() as logs, pytest.raises(StepAbandoned) as info:
        await bound(runtime, reads)(make_ctx())
    assert info.value.reason == "agent_profile_missing"
    assert runtime.requests == []
    (log,) = [log for log in logs if log["event"] == "agent_profile_missing"]
    assert log["log_level"] == "warning"
    assert (log["task_id"], log["step"], log["agent_id"]) == (str(TASK_ID), "plan", "A03")


async def test_build_input_errors_pass_through_without_a_run() -> None:
    async def broken(_ctx: StepContext) -> BaseModel:
        raise ValueError(SENTINEL)

    runtime, reads = FakeRuntime(ok), FakeReads()
    with pytest.raises(ValueError):
        await bound(runtime, reads, build_input=broken)(make_ctx())
    assert runtime.requests == [] and reads.outcome_calls == []


async def test_the_request_carries_ids_a_live_token_and_nothing_else() -> None:
    runtime = FakeRuntime(ok)
    await bound(runtime, FakeReads())(make_ctx())
    (request,) = runtime.requests
    assert isinstance(request.cancel, CancelToken) and not request.cancel.cancelled
    assert request.scenario is None and request.workspace is None
    assert (request.task_id, request.project_id, request.agent_id) == (TASK_ID, PROJECT_ID, "A03")


# --- registration -----------------------------------------------------------------------------------------


def make_binding(steps: dict[str, AgentStep], registry: Any = REGISTRY) -> dict[str, Any]:
    return bind_agent_steps(steps, runtime=FakeRuntime(ok), reads=FakeReads(), registry=registry)


def test_registration_accepts_the_runtime_agents_and_a_profile() -> None:
    steps = make_binding(
        {
            "intake": AgentStep("A02", build_payload),
            "plan": AgentStep("A03", build_payload),
            "implement": AgentStep("B14", build_payload),
        }
    )
    assert set(steps) == {"intake", "plan", "implement"}
    assert set(make_binding({"implement": AgentStep("B09", build_payload)})) == {"implement"}


def test_a_disabled_agent_still_registers() -> None:
    registry = registry_with("A02", enabled=False)
    assert set(make_binding({"intake": AgentStep("A02", build_payload)}, registry)) == {"intake"}


@pytest.mark.parametrize(
    ("name", "agent_id"),
    [("plan", "A02"), ("intake", "Z99"), ("context", "A04"), ("intake", "A01")],
)
def test_registration_refuses_the_wrong_agent(name: str, agent_id: str) -> None:
    with pytest.raises(ValueError, match="cannot run agent"):
        make_binding({name: AgentStep(agent_id, build_payload)})


def test_the_engine_reexports_what_a_step_may_use() -> None:
    assert engine.LeaseLost is lease.LeaseLost
    assert engine.TaskStatus is states.TaskStatus
