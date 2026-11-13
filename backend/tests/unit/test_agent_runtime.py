"""The AgentRuntime with fake ports (plan WP-3.1f2a Tasks 3 and 5; spec WP-3.1f Keputusan Lead #15-#24).

Task 3: the single model-call path. Every attempt, the first and each retry, runs ``before_attempt``
(heartbeat -> provider gate -> budget) before anything is sent, and reports to the ledger after; a refused
hook sends nothing and records nothing; a ledger failure fails the call as ``usage_unrecorded``.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.agents import contracts
from app.agents.grants import AgentNotRunnable
from app.agents.runtime import (
    CONTEXT_MAX_BYTES,
    MIN_OUTPUT_TOKENS_PER_S,
    NUDGE,
    REQUEST_BASE_S,
    SUBMIT_RESULT,
    AgentInputInvalid,
    AgentInputTooLarge,
    AgentRunResult,
    AgentRunUnfinished,
    BudgetBreach,
    BudgetRefused,
    LeaseLostError,
    PendingTool,
    ProviderBlocked,
    RunAlreadyFinished,
    RunEnd,
    ScenarioNotAllowed,
    StopReason,
    UsageUnrecorded,
    attempt_timeout_s,
    classify,
    run_cap,
)
from app.core.cancel import CancelToken
from app.core.redaction import Redactor
from app.llm.base import LLMError, Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from app.llm.scenarios.model import Fail, Reply
from app.models.enums import AGENT_RUN_ERROR_CODES
from app.tools.base import ToolResult
from app.tools.gateway import TaskMissing
from app.workflow.budget import BudgetConfigError, BudgetTaskMissing
from tests.runtime_fakes import (
    MODEL,
    OUTPUTS,
    PROMPTS,
    REGISTRY,
    STEP,
    UUID_A,
    FakeClock,
    FakeEvents,
    FakeGate,
    FakeHeartbeat,
    FakeLedger,
    FakeRuns,
    Fakes,
    a_model_call,
    agent_input,
    config,
    in_order,
    make_runtime,
    ok_result,
    registry_with,
    run_request,
    scripted,
    submit_turn,
    text_turn,
    tool_turn,
)

ONE_ROUND = ["beat", "gate", "budget", "send", "record"]
ANSWER = Reply((TextBlock("done"),))


async def test_every_attempt_beats_checks_the_gate_and_the_budget_first() -> None:
    fakes = Fakes(scripted(in_order(Fail("overloaded"), Fail("transient"), submit_turn({"summary": "s"}))))
    runtime = make_runtime(fakes)
    call = a_model_call()
    response = await runtime._call_model(call, fakes.providers.provider, cancel=CancelToken())
    assert response.stop_reason == "tool_use"
    assert fakes.log == ONE_ROUND * 3
    assert len(fakes.provider.requests) == 3
    assert [record.attempt for _, record in fakes.ledger.records] == [1, 2, 3]
    assert all(seen is call for seen in fakes.budget.calls)


async def test_every_attempt_of_a_run_beats_checks_the_gate_and_the_budget_first() -> None:
    """The same order on a run's own path, where each hook is bounded by the run's deadline: the run's beat
    and gate check, then beat, gate, budget before every attempt, and a last beat before ``succeeded``."""
    fakes = Fakes(scripted(in_order(Fail("overloaded"), submit_turn(VALID_IMPLEMENT))))
    assert (await run_b14(fakes)).status == "succeeded"
    assert fakes.log == ["beat", "gate", *ONE_ROUND * 2, "beat"]


REFUSALS = {
    "heartbeat": LeaseLostError("lost"),
    "gate": ProviderBlocked("PROVIDER_DISABLED", "kill_switch"),
    "budget": BudgetRefused("tokens", 10, 5, next_bound=8),
}


def _refuse(fakes: Fakes, hook: str, on_call: int) -> None:
    error = REFUSALS[hook]
    if hook == "heartbeat":
        fakes.heartbeat.refuse[on_call] = error
    elif hook == "gate":
        fakes.gate.refuse[on_call] = error
    else:
        fakes.budget.attempts.refuse[on_call] = error


@pytest.mark.parametrize("on_call", [1, 2], ids=["first-attempt", "retry"])
@pytest.mark.parametrize("hook", list(REFUSALS))
async def test_a_refused_hook_sends_nothing(hook: str, on_call: int) -> None:
    fakes = Fakes(scripted(in_order(Fail("overloaded"), ANSWER)))
    _refuse(fakes, hook, on_call)
    runtime = make_runtime(fakes)
    with pytest.raises(type(REFUSALS[hook])):
        await runtime._call_model(a_model_call(), fakes.providers.provider, cancel=CancelToken())
    sends = fakes.log.count("send")
    assert sends == on_call - 1
    assert fakes.log.count("record") == on_call - 1
    assert len(fakes.provider.requests) == sends
    assert fakes.log[-1] in {"beat", "gate", "budget"}  # nothing after the refusing hook


@pytest.mark.parametrize("script", [ANSWER, Fail("bad_request")], ids=["ok-attempt", "failed-attempt"])
async def test_a_ledger_failure_fails_the_call_as_usage_unrecorded(script: Reply | Fail) -> None:
    fakes = Fakes(scripted(script))
    cause = RuntimeError("database is down")
    fakes.ledger.counter.refuse[1] = cause
    runtime = make_runtime(fakes)
    with pytest.raises(UsageUnrecorded) as exc:
        await runtime._call_model(a_model_call(), fakes.providers.provider, cancel=CancelToken())
    assert exc.value.__cause__ is cause
    assert fakes.log == ONE_ROUND  # no retry after an unrecorded attempt


async def test_a_ledger_failure_is_not_retried_even_when_the_attempt_was_retryable() -> None:
    fakes = Fakes(scripted(in_order(Fail("overloaded"), ANSWER)))
    fakes.ledger.counter.refuse[1] = RuntimeError("database is down")
    with pytest.raises(UsageUnrecorded):
        await make_runtime(fakes)._call_model(a_model_call(), fakes.providers.provider, cancel=CancelToken())
    assert fakes.log.count("send") == 1


async def test_the_call_passes_the_cancel_token_and_the_retry_policy() -> None:
    fakes = Fakes(scripted(in_order(Fail("overloaded"), ANSWER)))
    cancel = CancelToken()
    cancel.cancel()
    with pytest.raises(LLMError) as exc:
        await make_runtime(fakes)._call_model(a_model_call(), fakes.providers.provider, cancel=cancel)
    assert exc.value.kind == "cancelled"
    assert fakes.log == []  # a cancelled call never reaches a hook


def test_budget_refused_keeps_its_numbers() -> None:
    refused = BudgetRefused("cost", Decimal("2"), None, next_bound=None)
    assert (refused.kind, refused.limit, refused.used, refused.next_bound) == (
        "cost",
        Decimal("2"),
        None,
        None,
    )
    blocked = ProviderBlocked("provider_not_ready", "no_report")
    assert (blocked.code, blocked.reason) == ("provider_not_ready", "no_report")


# === Task 5: the run loop ===================================================================================

ImplementOutput = contracts.ImplementOutput
VALID_IMPLEMENT = OUTPUTS["ImplementOutput"]


async def run_b14(fakes: Fakes, **kwargs: Any) -> AgentRunResult:
    runtime = make_runtime(fakes, **{k: v for k, v in kwargs.items() if k != "request"})
    return await runtime.run(kwargs.get("request") or run_request("B14"))


def ended(fakes: Fakes) -> RunEnd:
    assert len(fakes.runs.finishes) == 1
    return fakes.runs.finishes[0][1]


# --- refusals before any row --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent_id", "reason"),
    [
        ("Z99", "not an enabled agent"),
        ("A05", "not an enabled agent"),
        ("A04", "not run by the agent runtime"),
        ("A01", "absorbed"),
        ("C16", "absorbed"),
    ],
)
async def test_unknown_disabled_tool_mode_and_absorbed_agents_are_refused(agent_id: str, reason: str) -> None:
    """Each refusal by its own check (the checks overlap: a profile without a prompt is refused anyway)."""
    spec = REGISTRY.by_id(agent_id)
    if agent_id == "A05":
        assert spec is not None and spec.mvp_mode == "planned" and not spec.enabled
    if agent_id == "A04":
        assert spec is not None and spec.mvp_mode == "tool"
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    request = run_request(agent_id, runtime_id="B14")
    with pytest.raises(AgentNotRunnable, match=reason):
        await make_runtime(fakes).run(request)
    assert fakes.runs.starts == [] and fakes.log == []


async def test_input_of_another_contract_is_refused() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    with pytest.raises(AgentInputInvalid):
        await make_runtime(fakes).run(run_request("B14", runtime_id="A02"))
    assert fakes.runs.starts == []


def _intake_of_size(size: int) -> contracts.IntakeInput:
    """An IntakeInput whose JSON is exactly ``size`` bytes: a 60 000-byte prompt, padded by stack_summary."""

    def make(pad: int) -> contracts.IntakeInput:
        return contracts.IntakeInput(task_id=uuid.UUID(UUID_A), prompt="日" * 20_000, stack_summary="y" * pad)

    value = make(size - len(make(1).model_dump_json().encode("utf-8")) + 1)
    assert len(value.model_dump_json().encode("utf-8")) == size
    return value


@pytest.mark.parametrize(("size", "refused"), [(CONTEXT_MAX_BYTES, False), (CONTEXT_MAX_BYTES + 1, True)])
async def test_input_over_sixty_kilobytes_is_refused(size: int, refused: bool) -> None:
    fakes = Fakes(scripted(submit_turn(OUTPUTS["IntakeOutput"])))
    request = run_request("A02", task_id=uuid.UUID(UUID_A), agent_input=_intake_of_size(size))
    if refused:
        with pytest.raises(AgentInputTooLarge):
            await make_runtime(fakes).run(request)
        assert fakes.runs.starts == []
    else:
        assert (await make_runtime(fakes).run(request)).status == "succeeded"


async def test_a_scenario_is_refused_outside_scripted() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    runtime = make_runtime(fakes, runtime_config=config(scripted=False))
    with pytest.raises(ScenarioNotAllowed):
        await runtime.run(run_request("B14", scenario="calc"))
    assert fakes.runs.starts == []


# --- what is offered ----------------------------------------------------------------------------------------


async def test_only_built_and_granted_tools_are_offered_with_submit_result_last() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    await run_b14(fakes)
    tools = fakes.provider.requests[0].tools
    names = [t.name for t in tools]
    assert names[-1] == SUBMIT_RESULT and names.count(SUBMIT_RESULT) == 1
    assert "tests.run" not in names and "memory.retrieve_context" not in names  # in the grant, not built
    assert names[:-1] == sorted(names[:-1])
    spec = REGISTRY.by_id("B14")
    assert spec is not None and set(names[:-1]) < set(spec.tools)
    assert tools[-1].input_schema == ImplementOutput.model_json_schema()


async def test_offered_never_holds_submit_result() -> None:
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "README.md"}), submit_turn(VALID_IMPLEMENT)))
    await run_b14(fakes)
    ((ctx, _, _, _),) = fakes.tools.executed
    assert SUBMIT_RESULT not in ctx.offered
    assert ctx.offered == frozenset(t.name for t in fakes.provider.requests[0].tools[:-1])


async def test_a_tools_unavailable_agent_still_runs() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    result = await run_b14(fakes)
    assert result.status == "succeeded"


# --- submit_result ------------------------------------------------------------------------------------------


async def test_a_valid_submit_result_succeeds_with_the_validated_output() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    result = await run_b14(fakes)
    assert result.status == "succeeded" and result.error_code is None
    assert isinstance(result.output, ImplementOutput)
    assert result.output == ImplementOutput.model_validate(VALID_IMPLEMENT)
    end = ended(fakes)
    assert (end.status, end.iterations, end.output) == ("succeeded", 1, result.output.model_dump(mode="json"))


async def test_an_invalid_submit_result_is_an_error_result_and_counts_an_iteration() -> None:
    marker = "ECHO-ME-NOT-" + "q" * 20
    bad = {**VALID_IMPLEMENT, "changed_files": marker, marker: 1}
    fakes = Fakes(scripted(submit_turn(bad), submit_turn(VALID_IMPLEMENT)))
    result = await run_b14(fakes)
    assert result.status == "succeeded" and ended(fakes).iterations == 2
    (error,) = fakes.provider.requests[1].messages[-1].content
    assert isinstance(error, ToolResultBlock) and error.is_error
    assert error.tool_use_id == "toolu_submit"
    assert "changed_files" in error.content and "list_type" in error.content
    assert marker not in error.content


async def test_submit_result_never_reaches_the_gateway() -> None:
    fakes = Fakes(scripted(submit_turn({"summary": ""}), submit_turn(VALID_IMPLEMENT)))
    await run_b14(fakes)
    assert fakes.tools.executed == []


async def test_tools_beside_a_valid_submit_result_are_not_run() -> None:
    turn = Reply(
        (
            ToolUseBlock("toolu_read", "repo.read_file", {"path": "a"}),
            ToolUseBlock("toolu_submit", SUBMIT_RESULT, VALID_IMPLEMENT),
        ),
        stop_reason="tool_use",
    )
    fakes = Fakes(scripted(turn))
    assert (await run_b14(fakes)).status == "succeeded"
    assert fakes.tools.executed == []


async def test_tools_beside_an_invalid_submit_result_still_run() -> None:
    turn = Reply(
        (
            ToolUseBlock("toolu_read", "repo.read_file", {"path": "a"}),
            ToolUseBlock("toolu_submit", SUBMIT_RESULT, {}),
        ),
        stop_reason="tool_use",
    )
    fakes = Fakes(scripted(turn, submit_turn(VALID_IMPLEMENT)))
    assert (await run_b14(fakes)).status == "succeeded"
    assert [name for _, name, _, _ in fakes.tools.executed] == ["repo.read_file"]
    results = fakes.provider.requests[1].messages[-1].content
    assert [(r.tool_use_id, r.is_error) for r in results] == [("toolu_read", False), ("toolu_submit", True)]  # type: ignore[union-attr]


async def test_two_submit_results_in_one_turn_are_both_errors() -> None:
    turn = Reply(
        (
            ToolUseBlock("toolu_a", SUBMIT_RESULT, VALID_IMPLEMENT),
            ToolUseBlock("toolu_b", SUBMIT_RESULT, VALID_IMPLEMENT),
        ),
        stop_reason="tool_use",
    )
    fakes = Fakes(scripted(turn, submit_turn(VALID_IMPLEMENT)))
    assert (await run_b14(fakes)).status == "succeeded"
    results = fakes.provider.requests[1].messages[-1].content
    assert all(isinstance(r, ToolResultBlock) and r.is_error for r in results)
    assert all("one submit_result per turn" in r.content for r in results)  # type: ignore[union-attr]
    assert ended(fakes).iterations == 2


async def test_free_text_is_never_a_result() -> None:
    fakes = Fakes(scripted(text_turn(json.dumps(VALID_IMPLEMENT))))
    result = await run_b14(fakes)
    assert (result.status, result.error_code, result.output) == ("failed", "invalid_output", None)


# --- nudge --------------------------------------------------------------------------------------------------


async def test_end_turn_without_submit_result_gets_one_nudge() -> None:
    fakes = Fakes(scripted(text_turn(), submit_turn(VALID_IMPLEMENT)))
    assert (await run_b14(fakes)).status == "succeeded"
    assert fakes.provider.requests[1].messages[-1] == Message("user", NUDGE)


async def test_a_second_end_turn_fails_invalid_output() -> None:
    fakes = Fakes(scripted(text_turn(), text_turn()))
    result = await run_b14(fakes)
    assert (result.status, result.error_code) == ("failed", "invalid_output")
    assert len(fakes.provider.requests) == 2


async def test_end_turn_without_content_is_nudged() -> None:
    # No empty assistant message goes back (the API refuses one), so the turns are taken in order here.
    fakes = Fakes(scripted(in_order(Reply(()), submit_turn(VALID_IMPLEMENT))))
    assert (await run_b14(fakes)).status == "succeeded"
    second = fakes.provider.requests[1].messages
    assert second[-1] == Message("user", NUDGE)
    assert all(m.content for m in second)  # no empty message is sent


# --- limits -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("registry_max", "global_max", "cap"), [(8, 40, 8), (40, 5, 5)])
async def test_the_iteration_cap_is_the_smaller_limit(registry_max: int, global_max: int, cap: int) -> None:
    assert run_cap(registry_max, global_max) == cap
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"})))
    registry = registry_with("B14", limits={"max_iterations": registry_max})
    result = await run_b14(fakes, registry=registry, runtime_config=config(max_tool_iterations=global_max))
    assert (result.status, result.error_code) == ("failed", "iteration_limit")
    assert len(fakes.provider.requests) == cap == ended(fakes).iterations


async def test_a_looping_script_stops_at_the_cap() -> None:
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"})))
    result = await run_b14(fakes, runtime_config=config(max_tool_iterations=6))
    assert result.error_code == "iteration_limit"
    assert len(fakes.provider.requests) == 6


async def test_the_last_iteration_ending_without_a_result_is_iteration_limit() -> None:
    fakes = Fakes(scripted(text_turn()))
    result = await run_b14(fakes, runtime_config=config(max_tool_iterations=1))
    assert result.error_code == "iteration_limit"


# --- deadline -----------------------------------------------------------------------------------------------


async def test_a_run_past_its_wall_clock_fails_timeout_without_sending() -> None:
    clock = FakeClock()
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))

    async def slow_tool(ctx: Any, raw: object) -> Any:
        clock.now += 901  # B14 limits.timeout_seconds = 900
        return ok_result()

    fakes.tools.handlers["repo.read_file"] = slow_tool
    result = await run_b14(fakes, clock=clock)
    assert (result.status, result.error_code) == ("failed", "timeout")
    assert len(fakes.provider.requests) == 1
    assert fakes.log.count("send") == 1


async def test_the_deadline_cancels_an_attempt_in_flight() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    registry = registry_with("B14", limits={"timeout_seconds": 0.3})
    started = time.monotonic()
    result = await run_b14(fakes, registry=registry, clock=time.monotonic)
    assert time.monotonic() - started < 3
    assert (result.status, result.error_code) == ("failed", "timeout")
    ((_, record),) = fakes.ledger.records
    assert (record.status, record.error_kind) == ("cancelled", "cancelled")
    assert record.usage is not None and record.usage.output_tokens == 0  # partial, never unknown-as-zero


def test_attempt_timeout_follows_max_tokens_up_to_the_cap() -> None:
    assert attempt_timeout_s(1024, cap_s=120, remaining_s=10_000) == pytest.approx(55.6)
    assert attempt_timeout_s(8192, cap_s=120, remaining_s=10_000) == 120
    assert (REQUEST_BASE_S, MIN_OUTPUT_TOKENS_PER_S) == (30.0, 40.0)


def test_attempt_timeout_never_outlives_the_run() -> None:
    assert attempt_timeout_s(8192, cap_s=120, remaining_s=7.5) == 7.5


async def test_the_request_carries_the_attempt_timeout() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    await run_b14(fakes, runtime_config=config(max_output_tokens=1024), clock=FakeClock())
    request = fakes.provider.requests[0]
    assert request.max_tokens == 1024
    assert request.timeout_s == pytest.approx(55.6)


# --- lease --------------------------------------------------------------------------------------------------


async def test_a_lost_lease_before_an_attempt_sends_nothing() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.heartbeat.refuse[2] = LeaseLostError("lost")  # 1: the run's first beat; 2: before attempt 1
    result = await run_b14(fakes)
    assert (result.status, result.error_code) == ("failed", "lease_lost")
    assert fakes.provider.requests == [] and "send" not in fakes.log


async def test_a_lost_lease_before_a_tool_runs_nothing() -> None:
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))
    fakes.heartbeat.refuse[3] = LeaseLostError("lost")  # 3: before the tool
    result = await run_b14(fakes)
    assert (result.status, result.error_code) == ("failed", "lease_lost")
    assert fakes.tools.executed == []
    assert len(fakes.provider.requests) == 1


async def test_the_keep_alive_beats_during_a_long_attempt() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=0.3)
    result = await run_b14(fakes, runtime_config=config(heartbeat_interval_s=0.05), clock=time.monotonic)
    assert result.status == "succeeded"
    during = fakes.log[fakes.log.index("send") : fakes.log.index("record")]
    assert during.count("beat") >= 3


async def test_a_lease_lost_by_the_keep_alive_cancels_the_attempt() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    for call in range(3, 50):  # 1 and 2: the run's beat and the attempt's; then every keep-alive beat fails
        fakes.heartbeat.refuse[call] = LeaseLostError("lost")
    started = time.monotonic()
    result = await run_b14(fakes, runtime_config=config(heartbeat_interval_s=0.05), clock=time.monotonic)
    assert time.monotonic() - started < 3
    assert (result.status, result.error_code) == ("failed", "lease_lost")


async def test_tools_run_one_at_a_time() -> None:
    turn = Reply(
        (
            ToolUseBlock("toolu_a", "repo.read_file", {"path": "a"}),
            ToolUseBlock("toolu_b", "repo.read_file", {"path": "b"}),
            ToolUseBlock("toolu_c", "repo.get_tree", {}),
        ),
        stop_reason="tool_use",
    )
    fakes = Fakes(scripted(turn, submit_turn(VALID_IMPLEMENT)))
    running = 0
    overlaps = 0

    async def handler(ctx: Any, raw: object) -> Any:
        nonlocal running, overlaps
        running += 1
        overlaps += running > 1
        await asyncio.sleep(0.01)
        running -= 1
        return ok_result()

    fakes.tools.handlers["repo.read_file"] = handler
    fakes.tools.handlers["repo.get_tree"] = handler
    assert (await run_b14(fakes)).status == "succeeded"
    assert overlaps == 0
    assert [use_id for _, _, _, use_id in fakes.tools.executed] == ["toolu_a", "toolu_b", "toolu_c"]


# --- budget -------------------------------------------------------------------------------------------------


async def test_budget_is_checked_before_every_tool() -> None:
    turn = Reply(
        (
            ToolUseBlock("toolu_a", "repo.read_file", {"path": "a"}),
            ToolUseBlock("toolu_b", "repo.get_tree", {}),
        ),
        stop_reason="tool_use",
    )
    fakes = Fakes(scripted(turn, submit_turn(VALID_IMPLEMENT)))
    await run_b14(fakes)
    tool_part = [e for e in fakes.log if e.startswith(("budget:tool", "tool:"))]
    assert tool_part == ["budget:tool", "tool:repo.read_file", "budget:tool", "tool:repo.get_tree"]


async def test_a_budget_refusal_before_a_tool_ends_budget_exceeded() -> None:
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))
    fakes.budget.tools.refuse[1] = BudgetRefused("tool_iterations", 3, 3, next_bound=0)
    result = await run_b14(fakes)
    assert (result.status, result.error_code) == ("budget_exceeded", None)
    assert result.budget == BudgetBreach("tool_iterations", 3, 3, 0)
    assert fakes.tools.executed == []
    assert ended(fakes).budget == result.budget


async def test_a_budget_refusal_before_an_attempt_ends_budget_exceeded() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.budget.attempts.refuse[1] = BudgetRefused(
        "cost", Decimal("2"), Decimal("1.9"), next_bound=Decimal("0.2")
    )
    result = await run_b14(fakes)
    assert result.status == "budget_exceeded"
    assert result.budget == BudgetBreach("cost", Decimal("2"), Decimal("1.9"), Decimal("0.2"))
    assert fakes.provider.requests == []


# --- approval -----------------------------------------------------------------------------------------------


async def test_approval_required_stops_the_run_with_the_pending_tool() -> None:
    turn = Reply(
        (
            ToolUseBlock("toolu_r3", "repo.read_file", {"path": "a"}),
            ToolUseBlock("toolu_next", "repo.get_tree", {}),
        ),
        stop_reason="tool_use",
    )
    fakes = Fakes(scripted(turn, submit_turn(VALID_IMPLEMENT)))
    call_id = uuid.uuid4()

    async def needs_approval(ctx: Any, raw: object) -> ToolResult:
        return ToolResult(call_id, "approval_required", "waiting for approval", False)

    fakes.tools.handlers["repo.read_file"] = needs_approval
    result = await run_b14(fakes)
    assert (result.status, result.error_code) == ("approval_required", None)
    assert result.pending_tool == PendingTool("toolu_r3", "repo.read_file", call_id)
    assert ended(fakes).output == {
        "pending_tool": {
            "tool_use_id": "toolu_r3",
            "tool_name": "repo.read_file",
            "tool_call_id": str(call_id),
        }
    }
    assert [name for _, name, _, _ in fakes.tools.executed] == ["repo.read_file"]
    assert len(fakes.provider.requests) == 1


# --- how a run ends -----------------------------------------------------------------------------------------


def _chained(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


CLASSIFY: list[tuple[BaseException, StopReason | None, tuple[str, str | None]]] = [
    (ProviderBlocked("PROVIDER_DISABLED", "kill_switch"), None, ("failed", "PROVIDER_DISABLED")),
    (ProviderBlocked("provider_not_ready", "no_report"), None, ("failed", "provider_not_ready")),
    (LLMError("auth", status_code=401), None, ("failed", "provider_auth")),
    (UsageUnrecorded("x"), None, ("failed", "usage_unrecorded")),
    (UsageUnrecorded("x"), "cancelled", ("failed", "usage_unrecorded")),
    (_chained(asyncio.CancelledError(), UsageUnrecorded("x")), None, ("failed", "usage_unrecorded")),
    (_chained(LLMError("cancelled"), UsageUnrecorded("x")), "cancelled", ("failed", "usage_unrecorded")),
    (LeaseLostError("lost"), None, ("failed", "lease_lost")),
    (LLMError("cancelled"), "lease_lost", ("failed", "lease_lost")),
    (LLMError("cancelled"), "timeout", ("failed", "timeout")),
    (LLMError("cancelled"), "cancelled", ("cancelled", None)),
    *[
        (LLMError(kind), None, ("failed", "model_error"))  # type: ignore[arg-type]
        for kind in (
            "refusal",
            "bad_request",
            "malformed",
            "max_tokens",
            "rate_limited",
            "overloaded",
            "transient",
            "timeout",
        )
    ],
    (asyncio.CancelledError(), None, ("failed", "worker_cancelled")),
    (asyncio.CancelledError(), "cancelled", ("failed", "worker_cancelled")),
    (RuntimeError("db down"), None, ("failed", "internal_error")),
    (BudgetConfigError("bad budget"), None, ("failed", "internal_error")),
    (BudgetTaskMissing("gone"), None, ("failed", "internal_error")),
    (TaskMissing("gone"), None, ("failed", "internal_error")),
    (BudgetRefused("tokens", 1, 2, next_bound=3), None, ("budget_exceeded", None)),
]


@pytest.mark.parametrize(("error", "stop", "expected"), CLASSIFY)
def test_classify(error: BaseException, stop: StopReason | None, expected: tuple[str, str | None]) -> None:
    assert classify(error, stop) == expected


@pytest.mark.parametrize(
    "status", ["succeeded", "failed", "cancelled", "budget_exceeded", "approval_required"]
)
@pytest.mark.parametrize("code", [None, *AGENT_RUN_ERROR_CODES])
def test_every_failed_end_carries_a_code_and_no_other_does(status: str, code: str | None) -> None:
    # Every exit of the loop goes through RunEnd; this is its invariant over every status and code.
    budget = BudgetBreach("tokens", 1, 1, 0) if status == "budget_exceeded" else None
    if (status == "failed") == (code is not None):
        RunEnd(status, 0, code, budget=budget)  # type: ignore[arg-type]
    else:
        with pytest.raises(ValueError):
            RunEnd(status, 0, code, budget=budget)  # type: ignore[arg-type]


async def test_a_caller_cancel_mid_stream_ends_cancelled() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    cancel = CancelToken()
    runtime = make_runtime(fakes, clock=time.monotonic)
    task = asyncio.create_task(runtime.run(run_request("B14", cancel=cancel)))
    await asyncio.sleep(0.1)
    cancel.cancel()
    result = await asyncio.wait_for(task, 3)
    assert (result.status, result.error_code) == ("cancelled", None)
    ((_, record),) = fakes.ledger.records
    assert record.status == "cancelled"


async def test_an_asyncio_cancel_closes_the_run_worker_cancelled_and_propagates() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    task = asyncio.create_task(make_runtime(fakes, clock=time.monotonic).run(run_request("B14")))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    end = ended(fakes)
    assert (end.status, end.error_code) == ("failed", "worker_cancelled")
    assert fakes.providers.opened == fakes.providers.closed == 1


async def test_an_unexpected_error_closes_the_run_internal_error_and_propagates() -> None:
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))
    secret = "sk-ant-" + "unexpected-" + "x" * 24

    async def broken(ctx: Any, raw: object) -> ToolResult:
        raise RuntimeError(f"database is down ({secret})")

    fakes.tools.handlers["repo.read_file"] = broken
    runtime = make_runtime(fakes, redactor=Redactor([secret]))
    with pytest.raises(RuntimeError):
        await runtime.run(run_request("B14"))
    end = ended(fakes)
    assert (end.status, end.error_code) == ("failed", "internal_error")
    assert end.error is not None and "RuntimeError" in end.error and secret not in end.error


async def test_a_cancelled_run_whose_ledger_failed_is_usage_unrecorded() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    fakes.ledger.counter.refuse[1] = RuntimeError("database is down")
    cancel = CancelToken()
    task = asyncio.create_task(
        make_runtime(fakes, clock=time.monotonic).run(run_request("B14", cancel=cancel))
    )
    await asyncio.sleep(0.1)
    cancel.cancel()
    result = await asyncio.wait_for(task, 3)
    assert (result.status, result.error_code) == ("failed", "usage_unrecorded")


async def test_an_asyncio_cancelled_run_whose_ledger_failed_is_usage_unrecorded() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    fakes.ledger.counter.refuse[1] = RuntimeError("database is down")
    task = asyncio.create_task(make_runtime(fakes, clock=time.monotonic).run(run_request("B14")))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (ended(fakes).status, ended(fakes).error_code) == ("failed", "usage_unrecorded")


async def test_a_refusal_before_the_provider_still_closes_a_row() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.gate.refuse[1] = ProviderBlocked("PROVIDER_DISABLED", "kill_switch")
    result = await run_b14(fakes)
    assert (result.status, result.error_code) == ("failed", "PROVIDER_DISABLED")
    (start,) = fakes.runs.starts
    assert (start.model_provider, start.model_name) == (None, None)
    assert fakes.providers.opened == 0 and fakes.provider.requests == []
    assert result.agent_run_id == fakes.runs.ids[0]


async def test_a_profile_runs_with_its_runtime_contract_and_its_own_identity() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    result = await make_runtime(fakes).run(run_request("B09", runtime_id="B14"))
    assert isinstance(result.output, ImplementOutput)
    (start,) = fakes.runs.starts
    b09 = PROMPTS.get("B09")
    assert b09 is not None
    assert (start.agent_id, start.step, start.prompt_version) == ("B09", "implement", b09.version)
    request = fakes.provider.requests[0]
    assert request.system == b09.text
    assert request.metadata["agent_id"] == "B09"
    assert request.metadata["output_contract"] == "ImplementOutput"
    assert fakes.budget.calls[0].scope.agent_id == "B09"
    spec = REGISTRY.by_id("B09")
    assert spec is not None and start.agent_version == spec.version


async def test_the_run_start_names_the_model_and_the_input() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    request = run_request("B14")
    await make_runtime(fakes).run(request)
    (start,) = fakes.runs.starts
    assert (start.model_provider, start.model_name) == ("scripted", MODEL)
    assert (start.task_id, start.project_id) == (request.task_id, request.project_id)
    assert start.input == request.agent_input.model_dump(mode="json")
    first = fakes.provider.requests[0].messages[0]
    assert first.role == "user" and isinstance(first.content, str)
    assert first.content.startswith("<agent_input>\n") and first.content.endswith("\n</agent_input>")


async def test_the_input_is_redacted_before_it_is_sent() -> None:
    secret = "sk-ant-" + "input-" + "z" * 30
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    task_id = uuid.uuid4()
    agent_input_ = contracts.ImplementInput(
        task_id=task_id, step=STEP, requirements=[], gap_feedback=f"token={secret}"
    )
    await make_runtime(fakes, redactor=Redactor([secret])).run(
        run_request("B14", task_id=task_id, agent_input=agent_input_)
    )
    assert secret not in str(fakes.provider.requests[0].messages[0].content)


async def test_the_runtime_writes_no_task_title() -> None:
    fakes = Fakes(scripted(submit_turn(OUTPUTS["IntakeOutput"])))
    result = await make_runtime(fakes).run(run_request("A02"))
    assert isinstance(result.output, contracts.IntakeOutput)
    # After the answer only the final lease beat and the run's end: no other port (no task writer exists).
    assert fakes.log[-2:] == ["record", "beat"]
    assert len(fakes.runs.finishes) == 1 and fakes.events.usage_updates == 1


async def test_an_input_for_another_task_is_refused() -> None:
    """The input names the task it was built for; a run for another task refuses it (no row)."""
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    request = run_request("B14", agent_input=agent_input("B14", uuid.uuid4()))
    with pytest.raises(AgentInputInvalid, match="task"):
        await make_runtime(fakes).run(request)
    assert fakes.runs.starts == [] and fakes.log == []


async def test_a_run_cut_while_its_ledger_write_runs_reports_incomplete_usage() -> None:
    """Review H1: an attempt whose row was not written when the run closed (a second cancel while the
    shielded write of the cancelled attempt still waits, AG-13 N-1) makes the run's usage incomplete."""
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    gate, writing = asyncio.Event(), asyncio.Event()

    async def slow_record(record: Any) -> None:
        writing.set()
        await gate.wait()

    fakes.ledger.on_attempt = lambda call: slow_record  # type: ignore[method-assign]
    task = asyncio.create_task(make_runtime(fakes, clock=time.monotonic).run(run_request("B14")))
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.wait_for(writing.wait(), 5)
    task.cancel()  # the second cancel: the row is still not written
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    gate.set()
    assert ended(fakes).usage_complete is False


async def test_a_complete_run_reports_complete_usage() -> None:
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))
    assert (await run_b14(fakes)).status == "succeeded"
    assert ended(fakes).usage_complete is True


# --- hangs and locks (review of 42bcb2e: silent H2, AG-13 LOW-2, L2) ---------------------------------------


RELEASE = "release"  # every hanging fake below waits on its test's release event, set in the test's finally


class HangingHeartbeat(FakeHeartbeat):
    """Beats that do not return on the given calls (a held task row lock) until released."""

    def __init__(self, log: list[str], hang: set[int], release: asyncio.Event) -> None:
        super().__init__(log)
        self.hang = hang
        self.release = release

    async def beat(self, task_id: uuid.UUID) -> None:
        await super().beat(task_id)
        if self.calls in self.hang:
            await self.release.wait()


async def within(seconds: float, run: Any, release: asyncio.Event) -> tuple[bool, Any]:
    """Runs ``run`` for at most ``seconds``: (finished, its result or its exception). A run that hangs is a
    failure, never a stalled test run."""
    task = asyncio.ensure_future(run)
    try:
        done, _ = await asyncio.wait({task}, timeout=seconds)
    finally:
        release.set()
    if task not in done:
        await asyncio.wait({task}, timeout=5)
        return False, None
    return True, task.exception() or task.result()


async def test_a_failed_beat_is_retried_twice_before_the_lease_is_lost() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.heartbeat.refuse[2] = RuntimeError("database blip")
    fakes.heartbeat.refuse[3] = RuntimeError("database blip")  # two failures, then the third try works
    assert (await run_b14(fakes)).status == "succeeded"
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    for call in (2, 3, 4):
        fakes.heartbeat.refuse[call] = RuntimeError("database down")
    result = await run_b14(fakes)
    assert (result.status, result.error_code) == ("failed", "lease_lost")
    assert fakes.provider.requests == []


async def test_a_lost_lease_is_not_retried() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.heartbeat.refuse[2] = LeaseLostError("lost")
    result = await run_b14(fakes)
    assert result.error_code == "lease_lost"
    assert fakes.heartbeat.calls == 2


async def test_a_hanging_beat_is_a_lease_failure_within_its_bound() -> None:
    release = asyncio.Event()
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.heartbeat = HangingHeartbeat(fakes.log, {2, 3, 4}, release)
    runtime = make_runtime(fakes, runtime_config=config(beat_timeout_s=0.05), clock=time.monotonic)
    finished, result = await within(2, runtime.run(run_request("B14")), release)
    assert finished, "the run hung on a beat"
    assert (result.status, result.error_code) == ("failed", "lease_lost")
    assert fakes.provider.requests == []


async def test_the_keep_alive_retries_before_it_stops_the_run() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=0.5)
    fakes.heartbeat.refuse[3] = RuntimeError("database blip")  # the first keep-alive beat fails once
    result = await run_b14(fakes, runtime_config=config(heartbeat_interval_s=0.05), clock=time.monotonic)
    assert result.status == "succeeded"


class HangingGate(FakeGate):
    def __init__(self, log: list[str], release: asyncio.Event) -> None:
        super().__init__(log)
        self.release = release

    async def check(self, project_id: uuid.UUID) -> Any:
        self.tick()
        await self.release.wait()
        return self.models


async def test_a_hanging_port_ends_the_run_at_its_deadline() -> None:
    release = asyncio.Event()
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.gate = HangingGate(fakes.log, release)
    registry = registry_with("B14", limits={"timeout_seconds": 0.3})
    runtime = make_runtime(fakes, registry=registry, clock=time.monotonic)
    finished, result = await within(2, runtime.run(run_request("B14")), release)
    assert finished, "the run hung on a port past its deadline"
    assert (result.status, result.error_code) == ("failed", "timeout")


class HangingRuns(FakeRuns):
    def __init__(self, release: asyncio.Event) -> None:
        super().__init__()
        self.release = release

    async def finish(self, agent_run_id: uuid.UUID, end: RunEnd) -> Any:
        await self.release.wait()
        return await super().finish(agent_run_id, end)


async def test_a_hanging_close_is_bounded() -> None:
    release = asyncio.Event()
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.runs = HangingRuns(release)
    runtime = make_runtime(fakes, runtime_config=config(close_timeout_s=0.2), clock=time.monotonic)
    finished, outcome = await within(3, runtime.run(run_request("B14")), release)
    assert finished, "the close hung"
    assert isinstance(outcome, AgentRunUnfinished)


# --- end state (review of 42bcb2e: M1, M2, M5, L1, L3, worker cancel, error text) ---------------------------


class IgnoresCancel:
    """A provider that answers in full although the run's stop token fired (AG-13/silent PoC1)."""

    def __init__(self, inner: Any, delay: float) -> None:
        self.inner, self.delay, self.name = inner, delay, inner.name

    async def create_message(self, req: Any, *, on_text_delta: Any = None, cancel: Any = None) -> Any:
        await asyncio.sleep(self.delay)
        return await self.inner.create_message(req, on_text_delta=on_text_delta, cancel=None)

    async def aclose(self) -> None:
        return None


async def test_a_stop_during_an_attempt_wins_over_its_valid_submit_result() -> None:
    """M1 / PoC1: the lease was lost while the provider answered; the answer is not a success."""
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.providers.provider = IgnoresCancel(fakes.providers.provider, 0.4)
    for call in range(3, 50):
        fakes.heartbeat.refuse[call] = LeaseLostError("lost")
    result = await run_b14(fakes, runtime_config=config(heartbeat_interval_s=0.05), clock=time.monotonic)
    assert (result.status, result.error_code) == ("failed", "lease_lost")


async def test_a_final_beat_comes_before_success() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.heartbeat.refuse[3] = LeaseLostError("lost")  # 1: the run, 2: the attempt, 3: before the success
    result = await run_b14(fakes)
    assert (result.status, result.error_code) == ("failed", "lease_lost")
    assert fakes.log[-1] == "beat"


async def test_a_stop_during_the_last_tool_is_not_iteration_limit() -> None:
    """M2 / PoC2: the caller cancelled during the last allowed tool."""
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"})))
    cancel = CancelToken()

    async def tool(ctx: Any, raw: object) -> Any:
        cancel.cancel()
        await asyncio.sleep(0.05)
        return ok_result()

    fakes.tools.handlers["repo.read_file"] = tool
    runtime = make_runtime(fakes, runtime_config=config(max_tool_iterations=1))
    result = await runtime.run(run_request("B14", cancel=cancel))
    assert (result.status, result.error_code) == ("cancelled", None)


async def test_an_asyncio_cancel_from_outside_is_worker_cancelled() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    task = asyncio.create_task(make_runtime(fakes, clock=time.monotonic).run(run_request("B14")))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    end = ended(fakes)
    assert (end.status, end.error_code) == ("failed", "worker_cancelled")
    assert end.error is not None and "worker" in end.error


class FlakyRuns(FakeRuns):
    """``finish`` fails the given number of times first."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures
        self.failed: list[uuid.UUID] = []

    async def finish(self, agent_run_id: uuid.UUID, end: RunEnd) -> Any:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("database blip")
        return await super().finish(agent_run_id, end)

    async def fail(self, agent_run_id: uuid.UUID) -> None:
        self.failed.append(agent_run_id)


async def test_finish_is_retried_once() -> None:
    from structlog.testing import capture_logs

    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.runs = FlakyRuns(failures=1)
    request = run_request("B14")
    with capture_logs() as logs:
        result = await make_runtime(fakes).run(request)
    assert result.status == "succeeded"
    unfinished = [log for log in logs if log["event"] == "agent_run_unfinished"]
    assert len(unfinished) == 1
    assert unfinished[0]["task_id"] == str(request.task_id)
    assert unfinished[0]["agent_run_id"] == str(result.agent_run_id)
    assert unfinished[0]["correlation_id"] == f"agent_run:{result.agent_run_id}"


@pytest.mark.parametrize("path", ["normal", "error"])
async def test_a_finish_that_fails_twice_marks_the_run_failed(path: str) -> None:
    from structlog.testing import capture_logs

    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))
    runs = FlakyRuns(failures=2)
    fakes.runs = runs
    if path == "error":

        async def broken(ctx: Any, raw: object) -> Any:
            raise ValueError("boom")

        fakes.tools.handlers["repo.read_file"] = broken
    request = run_request("B14")
    with capture_logs() as logs:
        with pytest.raises(AgentRunUnfinished if path == "normal" else ValueError):
            await make_runtime(fakes).run(request)
    assert runs.failed == runs.ids  # the minimal UPDATE was tried on the run's row
    unfinished = [log for log in logs if log["event"] == "agent_run_unfinished"]
    assert len(unfinished) >= 2 and all(log["task_id"] == str(request.task_id) for log in unfinished)


class BrokenCancel(CancelToken):
    async def wait(self) -> None:
        raise RuntimeError("waited from another loop")


async def test_a_failing_watcher_is_logged_not_discarded() -> None:
    from structlog.testing import capture_logs

    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    with capture_logs() as logs:
        result = await make_runtime(fakes).run(run_request("B14", cancel=BrokenCancel()))
    assert result.status == "succeeded"
    failed = [log for log in logs if log["event"] == "agent_run_watcher_failed"]
    assert [log["error_type"] for log in failed] == ["RuntimeError"]


class SlowRuns(FakeRuns):
    async def finish(self, agent_run_id: uuid.UUID, end: RunEnd) -> Any:
        await asyncio.sleep(0.3)
        return await super().finish(agent_run_id, end)


async def test_a_second_cancel_during_the_close_still_records_the_end() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    fakes.runs = SlowRuns()
    task = asyncio.create_task(make_runtime(fakes, clock=time.monotonic).run(run_request("B14")))
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.sleep(0.1)
    task.cancel()  # during the close
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.5)
    assert ended(fakes).error_code == "worker_cancelled"


class BrokenEvents(FakeEvents):
    async def delta(self, scope: Any, text: str, *, reset: bool = False) -> None:
        raise RuntimeError("redis down")

    async def usage_updated(self, scope: Any) -> None:
        raise RuntimeError("database blip")


async def test_events_are_best_effort() -> None:
    from structlog.testing import capture_logs

    long_text = "The answer streams for a while before it is submitted. " * 20
    turn = Reply(
        (TextBlock(long_text), ToolUseBlock("toolu_submit", SUBMIT_RESULT, VALID_IMPLEMENT)),
        stop_reason="tool_use",
    )
    fakes = Fakes(scripted(turn))
    fakes.events = BrokenEvents()
    with capture_logs() as logs:
        result = await run_b14(fakes)
    assert result.status == "succeeded"
    failed = {
        (log["event_type"], log["error_type"]) for log in logs if log["event"] == "agent_run_event_failed"
    }
    assert failed == {("usage.updated", "RuntimeError"), ("agent.delta", "RuntimeError")}


async def test_no_usage_event_for_an_attempt_that_never_reached_the_provider() -> None:
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.budget.attempts.refuse[1] = BudgetRefused("tokens", 1, 0, next_bound=2)
    assert (await run_b14(fakes)).status == "budget_exceeded"
    assert fakes.events.usage_updates == 0


class ClosesBadly:
    name = "scripted"

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def create_message(self, req: Any, **kwargs: Any) -> Any:
        return await self.inner.create_message(req, **kwargs)

    async def aclose(self) -> None:
        raise RuntimeError("the connection pool was already gone")


async def test_a_failing_aclose_never_discards_a_finished_run() -> None:
    from structlog.testing import capture_logs

    from app.core.config import load_settings
    from app.services.agent_runtime import SettingsProviderFactory
    from tests.conftest import make_test_env

    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.providers = SettingsProviderFactory(  # type: ignore[assignment]
        load_settings(make_test_env()), build=lambda _: ClosesBadly(fakes.provider)
    )
    with capture_logs() as logs:
        result = await run_b14(fakes)
    assert result.status == "succeeded"
    assert [log["error_type"] for log in logs if log["event"] == "llm_provider_close_failed"] == [
        "RuntimeError"
    ]


async def test_error_texts_never_carry_the_exception_message() -> None:
    marker = "DETAIL-" + "q" * 12
    fakes = Fakes(scripted(Fail("refusal")))
    result = await run_b14(fakes)
    assert result.error == "LLMError: the model call failed (refusal)"
    fakes = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))

    async def broken(ctx: Any, raw: object) -> Any:
        raise RuntimeError(f"connection to db-host failed: {marker}")

    fakes.tools.handlers["repo.read_file"] = broken
    with pytest.raises(RuntimeError):
        await make_runtime(fakes).run(run_request("B14"))
    assert ended(fakes).error == "RuntimeError: an internal error ended the run"


async def test_a_usage_event_follows_a_call_whose_retry_was_refused() -> None:
    """Attempt 1 reached the provider (the ledger changed); the retry was refused by the budget."""
    fakes = Fakes(scripted(in_order(Fail("overloaded"), submit_turn(VALID_IMPLEMENT))))
    fakes.budget.attempts.refuse[2] = BudgetRefused("tokens", 1, 0, next_bound=2)
    assert (await run_b14(fakes)).status == "budget_exceeded"
    assert fakes.events.usage_updates == 1


class SlowToStopHeartbeat(FakeHeartbeat):
    """A beat that takes a while, and takes a while longer to stop when cancelled."""

    async def beat(self, task_id: uuid.UUID) -> None:
        await super().beat(task_id)
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)
            raise


async def test_a_second_cancel_while_the_watchers_stop_still_closes_the_run() -> None:
    """Review L1: stopping the watchers is shielded with the close, so a cancel in that window cannot leave
    the run without its end."""
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    fakes.heartbeat = SlowToStopHeartbeat(fakes.log)
    runtime = make_runtime(fakes, runtime_config=config(heartbeat_interval_s=0.05), clock=time.monotonic)
    task = asyncio.create_task(runtime.run(run_request("B14")))
    await asyncio.sleep(0.6)  # the keep-alive is mid-beat most of the time
    task.cancel()
    await asyncio.sleep(0.1)  # the watchers are stopping: the keep-alive takes 0.3 s to stop
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.0)
    assert ended(fakes).error_code == "worker_cancelled"


async def test_a_run_stopped_during_its_attempt_does_not_beat_again() -> None:
    """M1: the caller cancelled while the provider answered (ignoring the token): the answer is dropped at
    once, with no further port call (no final beat)."""
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.providers.provider = IgnoresCancel(fakes.providers.provider, 0.3)
    cancel = CancelToken()
    runtime = make_runtime(fakes, clock=time.monotonic)
    task = asyncio.create_task(runtime.run(run_request("B14", cancel=cancel)))
    await asyncio.sleep(0.1)
    cancel.cancel()
    result = await task
    assert (result.status, result.error_code) == ("cancelled", None)
    assert fakes.log[-1] == "record"


async def test_every_runtime_log_line_names_its_task_and_run() -> None:
    """Review L5: a runtime log line carries ``task_id`` and ``correlation_id`` ``agent_run:<id>``, so it can
    be found from the task and from the run."""
    from structlog.testing import capture_logs

    long_text = "The answer streams for a while before it is submitted. " * 20
    turn = Reply(
        (TextBlock(long_text), ToolUseBlock("toolu_submit", SUBMIT_RESULT, VALID_IMPLEMENT)),
        stop_reason="tool_use",
    )
    noisy = Fakes(scripted(turn))
    noisy.events = BrokenEvents()
    noisy.runs = FlakyRuns(failures=1)
    noisy.heartbeat.refuse[2] = RuntimeError("database blip")
    broken = Fakes(scripted(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))

    async def failing(ctx: Any, raw: object) -> Any:
        raise ValueError("boom")

    broken.tools.handlers["repo.read_file"] = failing
    stopped = Fakes(scripted(submit_turn(VALID_IMPLEMENT)), slow=5.0)
    runs: dict[str, Fakes] = {}
    with capture_logs() as logs:
        request = run_request("B14", cancel=BrokenCancel())
        runs[str(request.task_id)] = noisy
        assert (await make_runtime(noisy).run(request)).status == "succeeded"
        request = run_request("B14")
        runs[str(request.task_id)] = broken
        with pytest.raises(ValueError):
            await make_runtime(broken).run(request)
        request = run_request("B14")
        runs[str(request.task_id)] = stopped
        task = asyncio.create_task(make_runtime(stopped, clock=time.monotonic).run(request))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    lines = [log for log in logs if log["event"].startswith("agent_run_")]
    assert {line["event"] for line in lines} >= {
        "agent_run_event_failed",
        "agent_run_watcher_failed",
        "agent_run_beat_failed",
        "agent_run_unfinished",
        "agent_run_internal_error",
        "agent_run_worker_cancelled",
    }
    for line in lines:
        (run_id,) = runs[line["task_id"]].runs.ids
        assert line["agent_run_id"] == str(run_id), line
        assert line["correlation_id"] == f"agent_run:{run_id}", line


# --- delta review of 073e68d --------------------------------------------------------------------------------


class HangingLedger(FakeLedger):
    """The ledger write waits for ``release``: a usage row behind a held task-row lock."""

    def __init__(self, log: list[str], release: asyncio.Event) -> None:
        super().__init__(log)
        self.release = release

    def on_attempt(self, call: Any) -> Any:
        record = super().on_attempt(call)

        async def held(attempt: Any) -> None:
            await self.release.wait()
            await record(attempt)

        return held


async def test_a_hanging_ledger_write_is_usage_unrecorded_within_its_bound() -> None:
    """Review N1: a paid attempt's ledger write is bounded by the run's remaining time plus
    ``ledger_grace_s``; past that the attempt is unrecorded (``failed usage_unrecorded``), never a stalled
    run."""
    release = asyncio.Event()
    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.ledger = HangingLedger(fakes.log, release)
    registry = registry_with("B14", limits={"timeout_seconds": 0.3})
    runtime = make_runtime(
        fakes, registry=registry, runtime_config=config(ledger_grace_s=0.2), clock=time.monotonic
    )
    finished, result = await within(2, runtime.run(run_request("B14")), release)
    assert finished, "the run hung on the ledger write"
    assert (result.status, result.error_code) == ("failed", "usage_unrecorded")
    assert fakes.ledger.records == []


@pytest.mark.parametrize("port", ["gate", "budget", "tool"])
async def test_a_timeout_raised_inside_a_port_is_not_the_run_deadline(port: str) -> None:
    """Review N2: a ``TimeoutError`` a port raises itself (a database connect timeout, say) is that port's
    error, not the run's deadline: ``internal_error``, logged by type, while the run still had its time."""
    from structlog.testing import capture_logs

    fakes = Fakes(
        scripted(in_order(tool_turn("repo.read_file", {"path": "a"}), submit_turn(VALID_IMPLEMENT)))
    )
    if port == "gate":
        fakes.gate.refuse[1] = TimeoutError("db connect timed out")
    elif port == "budget":
        fakes.budget.attempts.refuse[1] = TimeoutError("db connect timed out")
    else:

        async def times_out(ctx: Any, raw: object) -> Any:
            raise TimeoutError("db connect timed out")

        fakes.tools.handlers["repo.read_file"] = times_out
    with capture_logs() as logs:
        with pytest.raises(TimeoutError):
            await make_runtime(fakes).run(run_request("B14"))
    assert (ended(fakes).status, ended(fakes).error_code) == ("failed", "internal_error")
    assert ended(fakes).error == "TimeoutError: an internal error ended the run"
    failed = [log for log in logs if log["event"] == "agent_run_internal_error"]
    assert [log["error_type"] for log in failed] == ["TimeoutError"]


class StreamsThenFails:
    """The first attempt streams some text, then fails with a transient error; later ones are the inner's."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.calls = 0

    async def create_message(self, req: Any, *, on_text_delta: Any = None, cancel: Any = None) -> Any:
        self.calls += 1
        if self.calls == 1:
            assert on_text_delta is not None
            await on_text_delta("partial text ")
            raise LLMError("overloaded", usage=Usage(input_tokens=5, output_tokens=1))
        return await self.inner.create_message(req, on_text_delta=on_text_delta, cancel=cancel)

    async def aclose(self) -> None:
        return None


async def test_an_attempt_stopped_during_its_restart_is_not_counted_as_sent() -> None:
    """Review N3: an attempt counts as sent only when its request goes out. A cancel during the restart (the
    reset event) stops the retry before its request: the one attempt sent is recorded, so the run's usage is
    complete, not partial."""
    holder: dict[str, asyncio.Task[Any]] = {}

    class CancelsOnReset(FakeEvents):
        async def delta(self, scope: Any, text: str, *, reset: bool = False) -> None:
            await super().delta(scope, text, reset=reset)
            if reset:
                holder["run"].cancel()
                await asyncio.sleep(5)

    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.providers.provider = StreamsThenFails(fakes.providers.provider)  # type: ignore[assignment]
    fakes.events = CancelsOnReset()
    holder["run"] = asyncio.create_task(make_runtime(fakes).run(run_request("B14")))
    with pytest.raises(asyncio.CancelledError):
        await holder["run"]
    end = ended(fakes)
    assert (end.error_code, end.usage_complete) == ("worker_cancelled", True)
    assert len(fakes.ledger.records) == 1 and fakes.events.resets == [True]


class AckLostRuns(FakeRuns):
    """The first finish commits, then its ack is lost; the retry finds the run already finished."""

    async def finish(self, agent_run_id: uuid.UUID, end: RunEnd) -> Any:
        if not self.finishes:
            await super().finish(agent_run_id, end)
            raise ConnectionError("the commit's ack was lost")
        raise RunAlreadyFinished(f"agent run {agent_run_id} is not running")


async def test_a_retried_finish_that_finds_the_run_finished_is_not_unfinished() -> None:
    """Review N4: the retry finds the row terminal, so the first write committed and only its ack was lost.
    That is logged as ``agent_run_already_finished`` with the run's ids, no minimal end is written over it,
    and the caller still gets ``AgentRunUnfinished``: the outcome is not known to it; the row is the truth."""
    from structlog.testing import capture_logs

    fakes = Fakes(scripted(submit_turn(VALID_IMPLEMENT)))
    fakes.runs = AckLostRuns()
    request = run_request("B14")
    with capture_logs() as logs:
        with pytest.raises(AgentRunUnfinished):
            await make_runtime(fakes).run(request)
    assert fakes.runs.failed_runs == []
    assert len(fakes.runs.finishes) == 1 and fakes.runs.finishes[0][1].status == "succeeded"
    lines = [log for log in logs if log["event"].startswith("agent_run_")]
    assert [(log["event"], log["attempt"]) for log in lines] == [
        ("agent_run_unfinished", 1),
        ("agent_run_already_finished", 2),
    ]
    (run_id,) = fakes.runs.ids
    assert lines[1]["task_id"] == str(request.task_id)
    assert lines[1]["correlation_id"] == f"agent_run:{run_id}"
