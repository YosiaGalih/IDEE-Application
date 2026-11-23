"""Pure mapping of how an agent run ended to what a workflow step returns (WP-3.2c2c2, spec §16.6.5 A).

No IO, no log, no clock. The inputs are strings, from the ``AgentRunResult`` a run returned or from a
``agent_runs`` row; the vocabulary below is matched against the runtime's by a unit test, so a new run code
without a mapping turns that test red (N55). The output is a verdict that the adapter returns or raises; the
engine alone writes task status.

Final table (``P`` is the pipeline of the step; the sentence of a ``fail`` is ``FAIL_MESSAGES[code]``):

- ``failed`` + ``PROVIDER_DISABLED`` / ``provider_not_ready`` / ``provider_auth``: ``P=draft`` fails with
  the lower-case code (N21); ``P=running`` interrupts with it (F31, a draft task cannot be interrupted).
- ``failed`` + ``timeout`` / ``iteration_limit`` / ``invalid_output`` / ``model_error`` / ``internal_error`` /
  ``usage_unrecorded``: fail with that code (N61, ``internal_error`` stays as it is).
- ``failed`` + ``lease_lost``: a ``LeaseLost`` verdict with a fixed message.
- ``failed`` + ``worker_cancelled`` / ``interrupted``: abandon ``run_worker_cancelled`` / ``run_interrupted``.
- ``cancelled``: abandon ``run_cancelled``. ``budget_exceeded``: fail ``budget_exceeded``.
- ``approval_required`` (result) or ``paused`` (row): fail ``approval_unsupported`` (N33, N62).
- row ``missing`` / ``running`` / ``finished`` + ``succeeded``: abandon ``run_outcome_missing`` /
  ``run_outcome_running`` / ``run_output_unread``.
- Anything else (a status or code outside the vocabulary, ``failed`` without a code, a code on another status,
  ``succeeded`` at ``verdict_for_end``): abandon ``run_outcome_unknown``.

``TASK_ERROR_CODES`` is the DATA_MODEL set of ``tasks.error_code``. It is a constant guarded by tests and is
NOT enforced at runtime: the writer of a failed task checks the pattern only (BC-16, ruling R-D5).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

from app.workflow.engine import Pipeline, StepAbandoned, StepResult
from app.workflow.lease import LeaseLost

# --- run vocabulary (strings; the runtime is not imported, a unit test matches them) --------------

PROVIDER_STOPS: Final = frozenset({"PROVIDER_DISABLED", "provider_not_ready", "provider_auth"})
RUN_FAILURES: Final = frozenset(
    {"timeout", "iteration_limit", "invalid_output", "model_error", "internal_error", "usage_unrecorded"}
)
RUN_ERROR_CODES: Final = PROVIDER_STOPS | RUN_FAILURES | {"lease_lost", "worker_cancelled", "interrupted"}
RUN_STATUSES: Final = frozenset({"succeeded", "failed", "cancelled", "budget_exceeded", "approval_required"})

# --- closed sets (N55) ----------------------------------------------------------------------------

TASK_ERROR_CODES: Final = frozenset(
    {
        "provider_not_ready",
        "provider_disabled",
        "provider_auth",
        "usage_unrecorded",
        "invalid_output",
        "iteration_limit",
        "timeout",
        "model_error",
        "internal_error",
        "planning_interrupted",
        "step_unavailable",
        "step_result_invalid",
        "duration_exceeded",
        "budget_exceeded",
        "approval_unsupported",
        "agent_unavailable",
        "agent_input_invalid",
        "worktree_failed",
        "commit_failed",
    }
)
STEP_INTERRUPT_REASONS: Final = frozenset({"provider_not_ready", "provider_disabled", "provider_auth"})
ABANDON_REASONS: Final = frozenset(
    {
        "run_outcome_missing",
        "run_outcome_running",
        "run_output_unread",
        "run_worker_cancelled",
        "run_interrupted",
        "run_cancelled",
        "run_outcome_unknown",
        "agent_profile_missing",
    }
)

# Fixed sentences (S24, RD-6, BC-26): never the run's error text, an exception or an output.
FAIL_MESSAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "provider_not_ready": "Agent {agent_id} could not run: the provider is not ready",
        "provider_disabled": "Agent {agent_id} could not run: the provider is switched off",
        "provider_auth": "Agent {agent_id} stopped: the provider refused its credentials",
        "timeout": "Agent {agent_id} ran past its time limit",
        "iteration_limit": "Agent {agent_id} used its iterations without a valid result",
        "invalid_output": "Agent {agent_id} ended its turn without submitting a result",
        "model_error": "Agent {agent_id} stopped: the model call failed",
        "internal_error": "Agent {agent_id} stopped: an internal error ended the run",
        "usage_unrecorded": "Agent {agent_id} stopped: the usage of a model call was not recorded",
        "budget_exceeded": "Agent {agent_id} stopped: the task budget is used up",
        "approval_unsupported": (
            "Agent {agent_id} asked for an approval, which this version cannot handle yet"
        ),
        "agent_unavailable": "Agent {agent_id} is not available to run",
        "agent_input_invalid": "Agent {agent_id} could not run: its input was not valid",
    }
)

_LEASE_LOST_MESSAGE: Final = "the agent run lost the task lease"

RunVerdict = StepResult | StepAbandoned | LeaseLost  # returned, never raised here; the adapter raises


def task_code(run_code: str) -> str:
    """The task-side spelling of a run code (N21): ``PROVIDER_DISABLED`` becomes ``provider_disabled``."""
    return run_code.lower()


def _fail(code: str, agent_id: str) -> StepResult:
    return StepResult.fail(code, FAIL_MESSAGES[code].replace("{agent_id}", agent_id))


def refused(code: Literal["agent_unavailable", "agent_input_invalid"], agent_id: str) -> StepResult:
    """The result of a step that never started a run (N56, registry or input trouble)."""
    return _fail(code, agent_id)


def _unknown() -> StepAbandoned:
    return StepAbandoned("run_outcome_unknown")


def verdict_for_end(status: str, error_code: str | None, *, pipeline: Pipeline, agent_id: str) -> RunVerdict:
    """The verdict for a run end other than ``succeeded``: a returned result, or a ``finished`` row."""
    if status == "failed":
        if error_code in PROVIDER_STOPS:
            code = task_code(error_code)
            if pipeline == "running":
                return StepResult.interrupt(code)
            return _fail(code, agent_id)
        if error_code in RUN_FAILURES:
            return _fail(error_code, agent_id)
        if error_code == "lease_lost":
            return LeaseLost(_LEASE_LOST_MESSAGE)
        if error_code == "worker_cancelled":
            return StepAbandoned("run_worker_cancelled")
        if error_code == "interrupted":
            return StepAbandoned("run_interrupted")
        return _unknown()
    if error_code is not None:
        return _unknown()
    if status == "cancelled":
        return StepAbandoned("run_cancelled")
    if status == "budget_exceeded":
        return _fail("budget_exceeded", agent_id)
    if status == "approval_required":
        return _fail("approval_unsupported", agent_id)
    return _unknown()  # succeeded is handled by the adapter before this call; any other status is foreign


def verdict_for_row(
    state: str, status: str | None, error_code: str | None, *, pipeline: Pipeline, agent_id: str
) -> RunVerdict:
    """The verdict for a ``RunOutcome`` of ``read_run_outcome`` (missing/running/paused/finished)."""
    if state == "missing":
        return StepAbandoned("run_outcome_missing")
    if state == "running":
        return StepAbandoned("run_outcome_running")
    if state == "paused":
        return _fail("approval_unsupported", agent_id)
    if state == "finished":
        if status == "succeeded" and error_code is None:
            return StepAbandoned("run_output_unread")
        return verdict_for_end(status or "", error_code, pipeline=pipeline, agent_id=agent_id)
    return _unknown()
