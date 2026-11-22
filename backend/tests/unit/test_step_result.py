"""Step result contract (spec WP-3.2 §16.4 #75, §16.5, §16.5a #6): codes are checked when a result is made,
and the per-pipeline allowlist is narrower than the state machine."""

from __future__ import annotations

import pytest

from app.workflow.engine import ALLOWED_STEP_TARGETS, PIPELINE, StepAbandoned, StepResult
from app.workflow.states import ALLOWED, TaskStatus, check_error_code

S = TaskStatus
BAD_CODES = ["", "BAD", "Bad_code", "1abc", "_abc", "a-b", "a b", "a.b", "a" * 65, "abc\n"]


@pytest.mark.parametrize("code", BAD_CODES)
def test_a_bad_code_is_refused_when_the_result_is_made(code: str) -> None:
    with pytest.raises(ValueError, match="error_code"):
        StepResult.fail(code, "message")
    with pytest.raises(ValueError, match="reason"):
        StepResult.interrupt(code)
    with pytest.raises(ValueError, match="reason"):
        StepAbandoned(code)


def test_a_direct_construction_is_checked_too() -> None:
    with pytest.raises(ValueError, match="error_code"):
        StepResult("fail")
    with pytest.raises(ValueError, match="reason"):
        StepResult("interrupt")


def test_an_unknown_kind_or_a_transition_without_a_target_is_refused() -> None:
    """Task 1 review: an unknown kind passed through as no stop at all, so the pipeline went on silently."""
    with pytest.raises(ValueError, match="kind"):
        StepResult("skip")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="to"):
        StepResult("transition")


def test_good_codes_are_kept() -> None:
    assert StepResult.fail("intake_rejected", "m") == StepResult(
        "fail", error_code="intake_rejected", message="m"
    )
    assert StepResult.interrupt("validation_unavailable") == StepResult(
        "interrupt", reason="validation_unavailable"
    )
    assert StepAbandoned("provider_not_ready").reason == "provider_not_ready"
    check_error_code("a" * 64, "error_code")  # the longest tasks.error_code fits


def test_check_error_code_names_what_it_checked_and_never_echoes_the_value() -> None:
    secret = "sk-test-" + "secret-0123"
    with pytest.raises(ValueError, match=r"^reason must be snake_case, 1-64 characters$"):
        check_error_code(secret, "reason")
    with pytest.raises(ValueError, match=r"^error_code must be snake_case, 1-64 characters$"):
        check_error_code(None, "error_code")  # type: ignore[arg-type]


def test_the_allowlist_is_narrower_than_the_state_machine() -> None:
    assert ALLOWED_STEP_TARGETS == {
        "draft": {S.PLANNED, S.NEEDS_CLARIFICATION},
        "running": {S.INTERRUPTED},
    }
    assert set(ALLOWED_STEP_TARGETS) == set(PIPELINE)
    for status, targets in ALLOWED_STEP_TARGETS.items():
        assert targets <= ALLOWED[S(status)]
        assert not targets & {S.COMPLETED, S.CANCELLED, S.FAILED}  # failed only through StepResult.fail


def test_a_target_is_coerced_to_a_task_status() -> None:
    assert StepResult("transition", to="planned").to is S.PLANNED  # type: ignore[arg-type]
    assert StepResult.transition(S.PLANNED, None).to is S.PLANNED
    assert StepResult.done().to is None


def test_an_invalid_target_string_is_refused() -> None:
    with pytest.raises(ValueError):
        StepResult("transition", to="not_a_status")  # type: ignore[arg-type]
