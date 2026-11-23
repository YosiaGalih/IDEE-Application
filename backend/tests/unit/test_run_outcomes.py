"""Pure mapping of a run's end to a step verdict (WP-3.2c2c2 Task 1; spec §16.6.5 A, final table #8)."""

from __future__ import annotations

import itertools
from typing import Literal, get_args

import pytest

from app.agents.runtime import RunErrorCode, RunStatus
from app.models.enums import AGENT_RUN_ERROR_CODES, AGENT_RUN_STATUSES
from app.workflow import engine
from app.workflow.engine import Pipeline, StepAbandoned, StepResult
from app.workflow.lease import LeaseLost
from app.workflow.run_outcomes import (
    ABANDON_REASONS,
    FAIL_MESSAGES,
    PROVIDER_STOPS,
    RUN_ERROR_CODES,
    RUN_FAILURES,
    RUN_STATUSES,
    STEP_INTERRUPT_REASONS,
    TASK_ERROR_CODES,
    refused,
    task_code,
    verdict_for_end,
    verdict_for_row,
)
from app.workflow.states import check_error_code

PIPELINES: tuple[Pipeline, ...] = ("draft", "running")
STATUSES = sorted(RUN_STATUSES | {"weird"})
CODES: list[str | None] = [*sorted(RUN_ERROR_CODES), None, "provider_new"]


def shape(verdict: object) -> tuple[str, ...]:
    """A comparable form of a verdict, so the expectation below is written apart from the implementation."""
    if isinstance(verdict, StepResult):
        return (verdict.kind, verdict.error_code or verdict.reason or "")
    if isinstance(verdict, StepAbandoned):
        return ("abandon", verdict.reason)
    if isinstance(verdict, LeaseLost):
        return ("lease_lost", str(verdict))
    raise AssertionError(f"unexpected verdict type {type(verdict).__name__}")


def expected_end(status: str, code: str | None, pipeline: Pipeline) -> tuple[str, ...]:
    unknown = ("abandon", "run_outcome_unknown")
    if status == "failed":
        if code in ("PROVIDER_DISABLED", "provider_not_ready", "provider_auth"):
            assert code is not None
            return ("fail" if pipeline == "draft" else "interrupt", code.lower())
        if code in (
            "timeout",
            "iteration_limit",
            "invalid_output",
            "model_error",
            "internal_error",
            "usage_unrecorded",
        ):
            assert code is not None
            return ("fail", code)
        if code == "lease_lost":
            return ("lease_lost", "the agent run lost the task lease")
        if code == "worker_cancelled":
            return ("abandon", "run_worker_cancelled")
        if code == "interrupted":
            return ("abandon", "run_interrupted")
        return unknown
    if code is not None:
        return unknown
    if status == "cancelled":
        return ("abandon", "run_cancelled")
    if status == "budget_exceeded":
        return ("fail", "budget_exceeded")
    if status == "approval_required":
        return ("fail", "approval_unsupported")
    return unknown


def end(status: str, code: str | None, pipeline: Pipeline = "running", agent_id: str = "A02"):
    return verdict_for_end(status, code, pipeline=pipeline, agent_id=agent_id)


def reachable_fail_codes() -> set[str]:
    found: set[str] = set()
    for status, code, pipeline in itertools.product(STATUSES, CODES, PIPELINES):
        verdict = end(status, code, pipeline)
        if isinstance(verdict, StepResult) and verdict.kind == "fail":
            found.add(verdict.error_code)
    return found


# --- vocabulary -----------------------------------------------------------------------------------


def test_run_vocabulary_matches_runtime_and_db() -> None:
    assert RUN_ERROR_CODES == set(get_args(RunErrorCode)) == set(AGENT_RUN_ERROR_CODES)
    assert RUN_STATUSES == set(get_args(RunStatus)) == set(AGENT_RUN_STATUSES) - {"running"}
    assert PROVIDER_STOPS | RUN_FAILURES < RUN_ERROR_CODES


def test_closed_sets_have_the_designed_size() -> None:
    assert len(TASK_ERROR_CODES) == 19
    assert len(ABANDON_REASONS) == 8
    assert len(FAIL_MESSAGES) == 13
    assert STEP_INTERRUPT_REASONS == {"provider_not_ready", "provider_disabled", "provider_auth"}


def test_task_code_lowercases() -> None:
    assert task_code("PROVIDER_DISABLED") == "provider_disabled"
    assert task_code("timeout") == "timeout"


# --- matrix ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("pipeline", PIPELINES)
@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("status", STATUSES)
def test_verdict_for_end_matrix(status: str, code: str | None, pipeline: Pipeline) -> None:
    assert shape(end(status, code, pipeline)) == expected_end(status, code, pipeline)


@pytest.mark.parametrize("pipeline", PIPELINES)
@pytest.mark.parametrize("code", CODES)
@pytest.mark.parametrize("status", STATUSES)
def test_verdict_properties(status: str, code: str | None, pipeline: Pipeline) -> None:
    verdict = end(status, code, pipeline)
    if isinstance(verdict, StepResult):
        assert verdict.kind in ("fail", "interrupt")  # never done or transition
        if verdict.kind == "interrupt":
            assert pipeline == "running"  # F31: a draft task cannot be interrupted
            assert verdict.reason in STEP_INTERRUPT_REASONS
        else:
            assert verdict.error_code in TASK_ERROR_CODES
            assert verdict.error_code == verdict.error_code.lower()
    elif isinstance(verdict, StepAbandoned):
        assert verdict.reason in ABANDON_REASONS
        check_error_code(verdict.reason, "reason")
    else:
        assert isinstance(verdict, LeaseLost)


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_provider_disabled_is_lowercased_in_both_pipelines(pipeline: Pipeline) -> None:
    verdict = end("failed", "PROVIDER_DISABLED", pipeline)
    assert isinstance(verdict, StepResult)
    assert (verdict.error_code or verdict.reason) == "provider_disabled"


def test_lease_lost_verdict_carries_no_owner() -> None:
    assert str(end("failed", "lease_lost")) == "the agent run lost the task lease"


# --- rows -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_verdict_for_row_states(pipeline: Pipeline) -> None:
    def row(state: str, status: str | None = None, code: str | None = None):
        return shape(verdict_for_row(state, status, code, pipeline=pipeline, agent_id="A02"))

    assert row("missing") == ("abandon", "run_outcome_missing")
    assert row("running") == ("abandon", "run_outcome_running")
    assert row("paused") == ("fail", "approval_unsupported")  # N33/N62
    assert row("finished", "succeeded") == ("abandon", "run_output_unread")
    assert row("finished", "failed", "internal_error") == ("fail", "internal_error")  # N61
    assert row("finished", "budget_exceeded") == ("fail", "budget_exceeded")
    assert row("finished", "cancelled") == ("abandon", "run_cancelled")
    assert row("finished", "failed", "worker_cancelled") == ("abandon", "run_worker_cancelled")
    assert row("finished", "failed", "interrupted") == ("abandon", "run_interrupted")
    assert row("finished", "failed", "lease_lost") == ("lease_lost", "the agent run lost the task lease")
    assert row("finished", "failed", None) == ("abandon", "run_outcome_unknown")
    assert row("elsewhere") == ("abandon", "run_outcome_unknown")


def test_internal_error_is_never_budget_exceeded() -> None:  # S20
    for pipeline in PIPELINES:
        verdict = verdict_for_row("finished", "failed", "internal_error", pipeline=pipeline, agent_id="A02")
        assert shape(verdict) != ("fail", "budget_exceeded")


# --- sentences ------------------------------------------------------------------------------------


def test_fail_message_keys_are_the_reachable_fail_codes_and_refusals() -> None:
    assert set(FAIL_MESSAGES) == reachable_fail_codes() | {"agent_unavailable", "agent_input_invalid"}


@pytest.mark.parametrize("code", sorted(FAIL_MESSAGES))
def test_fail_messages_are_fixed_sentences(code: str) -> None:
    text = FAIL_MESSAGES[code].replace("{agent_id}", "A02")
    assert "{" not in text and "}" not in text
    assert text.isascii()
    assert len(text) <= 200
    assert "A02" in text


def test_fail_verdict_message_names_the_agent() -> None:
    verdict = end("failed", "timeout", "running", "B14")
    assert isinstance(verdict, StepResult)
    assert verdict.message == FAIL_MESSAGES["timeout"].replace("{agent_id}", "B14")


def test_fail_messages_is_read_only() -> None:
    with pytest.raises(TypeError):
        FAIL_MESSAGES["timeout"] = "x"  # type: ignore[index]


@pytest.mark.parametrize("code", ["agent_unavailable", "agent_input_invalid"])
def test_refused(code: Literal["agent_unavailable", "agent_input_invalid"]) -> None:
    assert refused(code, "B14") == StepResult.fail(code, FAIL_MESSAGES[code].replace("{agent_id}", "B14"))


def test_agent_id_is_replaced_literally() -> None:
    assert "{agent_id}" not in end("failed", "timeout", "draft", "{x}").message  # type: ignore[union-attr]


# --- engine-owned codes ---


def test_engine_codes_are_in_the_closed_set() -> None:
    owned = {"planning_interrupted", "step_unavailable", "step_result_invalid", engine.DURATION_EXCEEDED}
    assert owned <= TASK_ERROR_CODES
    assert all(code == code.lower() for code in TASK_ERROR_CODES)
    assert not {"lease_lost", "worker_cancelled", "interrupted"} & TASK_ERROR_CODES
