"""The adapter that makes an agent run a workflow step (WP-3.2c2c2, spec §16.6.5 B; N56-N59, N63).

A step of this kind builds the agent's input, asks the runtime for one run under a fresh id and returns what
the run's end means to the engine: a ``StepResult``, or a raised ``StepAbandoned`` / ``LeaseLost``. It never
writes task status; the engine does, from what the step returns. The mapping of a run's end is pure and lives
in ``app.workflow.run_outcomes``.

Log fields: ids, the fixed run vocabulary and error types only. Never the run's error text, its output, an
exception's message, an owner or a token, and never ``exc_info`` (RD-6, S24).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.grants import AgentNotRunnable
from app.agents.registry import AgentRegistry
from app.agents.runtime import (
    ABSORBED_PROFILES,
    STEPS,
    AgentInputInvalid,
    AgentRunIdConflict,
    AgentRunRequest,
    AgentRunResult,
    ScenarioNotAllowed,
)
from app.core.db import bound_transaction
from app.core.logging import get_logger
from app.models.agents import AgentProfile
from app.services.agent_runtime import RunOutcome, read_run_outcome
from app.workflow.engine import LeaseLost, Step, StepAbandoned, StepContext, StepResult
from app.workflow.run_outcomes import (
    RUN_ERROR_CODES,
    RUN_STATUSES,
    RunVerdict,
    refused,
    verdict_for_end,
    verdict_for_row,
)

# A database read of the adapter (profile row, run row) is bounded by this wall-clock time; read at call time.
AGENT_STEP_READ_TIMEOUT_S: Final = 15.0

# A status or a code read from a database row is logged only when it is in the closed vocabulary; any other
# value is cut to a fixed word (a row may hold anything, and a log line is outside redaction). A row that is
# still ``running`` has the status ``running``, which a run's end never has.
_LOGGED_STATUSES: Final = RUN_STATUSES | {"running"}
_UNRECOGNISED: Final = "unrecognised"

logger = get_logger(__name__)


def _bounded(value: str | None, vocabulary: frozenset[str]) -> str | None:
    if value is None or value in vocabulary:
        return value
    return _UNRECOGNISED


@dataclass(frozen=True, slots=True)
class AgentSuccess:
    """What ``on_success`` gets: the run that succeeded and its output, in memory and not yet redacted. The
    output is never logged, stored or emitted as it is (the mapper of a later step redacts)."""

    agent_run_id: uuid.UUID
    agent_id: str
    output: BaseModel = field(repr=False)


BuildInput = Callable[[StepContext], Awaitable[BaseModel]]
OnSuccess = Callable[[StepContext, AgentSuccess], Awaitable[StepResult]]


async def done(ctx: StepContext, success: AgentSuccess) -> StepResult:
    """The default ``on_success``: the step is done."""
    return StepResult.done()


@dataclass(frozen=True, slots=True)
class AgentStep:
    agent_id: str
    build_input: BuildInput
    on_success: OnSuccess = done


class AgentRunner(Protocol):
    async def run(self, request: AgentRunRequest) -> AgentRunResult: ...


class AgentStepReads(Protocol):
    async def profile_exists(self, agent_id: str) -> bool: ...

    async def run_outcome(
        self, agent_run_id: uuid.UUID, *, task_id: uuid.UUID, agent_id: str
    ) -> RunOutcome: ...


class DbAgentStepReads:
    """The two reads of the adapter, each in its own short transaction under ``bound_transaction`` and a
    wall-clock bound. A read error is the step's bug or an outage: it propagates."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def profile_exists(self, agent_id: str) -> bool:
        async with asyncio.timeout(AGENT_STEP_READ_TIMEOUT_S):
            async with self._sessions() as session:
                await bound_transaction(session)
                found = await session.scalar(
                    select(AgentProfile.agent_id).where(AgentProfile.agent_id == agent_id)
                )
                return found is not None

    async def run_outcome(self, agent_run_id: uuid.UUID, *, task_id: uuid.UUID, agent_id: str) -> RunOutcome:
        async with asyncio.timeout(AGENT_STEP_READ_TIMEOUT_S):
            async with self._sessions() as session:
                await bound_transaction(session)
                return await read_run_outcome(session, agent_run_id, task_id=task_id, agent_id=agent_id)


def validate_agent_steps(steps: Mapping[str, AgentStep], registry: AgentRegistry) -> None:
    """Refuses, without any SQL or file read, an agent the runtime cannot run as that step (a bad wiring is a
    bug, found at assembly). A disabled agent is accepted: it fails at run time with ``agent_unavailable``,
    so an operator can switch an agent off."""
    for name, spec in steps.items():
        entry = registry.by_id(spec.agent_id)
        runtime_id = None if entry is None else entry.runtime_id
        if runtime_id is None or spec.agent_id in ABSORBED_PROFILES or STEPS.get(runtime_id) != name:
            raise ValueError(f"step {name} cannot run agent {spec.agent_id}")


def bind_agent_steps(
    steps: Mapping[str, AgentStep], *, runtime: AgentRunner, reads: AgentStepReads, registry: AgentRegistry
) -> dict[str, Step]:
    """The engine steps of the registered agent steps, after ``validate_agent_steps``."""
    validate_agent_steps(steps, registry)
    return {name: _bind(name, spec, runtime, reads) for name, spec in steps.items()}


def _log_ended(
    ctx: StepContext,
    agent_id: str,
    run_id: uuid.UUID,
    *,
    source: Literal["result", "row"],
    run_status: str | None,
    run_error_code: str | None,
    verdict: str,
) -> None:
    logger.info(
        "agent_step_run_ended",
        task_id=str(ctx.task_id),
        step=ctx.step,
        agent_id=agent_id,
        agent_run_id=str(run_id),
        source=source,
        run_status=_bounded(run_status, _LOGGED_STATUSES),
        run_error_code=_bounded(run_error_code, RUN_ERROR_CODES),
        verdict=verdict,
    )


def _verdict_name(verdict: RunVerdict) -> str:
    if isinstance(verdict, StepResult):
        return verdict.kind
    if isinstance(verdict, StepAbandoned):
        return "abandoned"
    return "lease_lost"


def _deliver(
    ctx: StepContext,
    verdict: RunVerdict,
    agent_id: str,
    run_id: uuid.UUID,
    run_status: str | None,
    run_error_code: str | None,
) -> StepResult:
    """A returned result is returned, a verdict that is an exception is raised (and logged when unknown)."""
    if isinstance(verdict, StepResult):
        return verdict
    if isinstance(verdict, StepAbandoned) and verdict.reason == "run_outcome_unknown":
        logger.error(
            "run_outcome_unknown",
            task_id=str(ctx.task_id),
            step=ctx.step,
            agent_id=agent_id,
            agent_run_id=str(run_id),
            run_status=_bounded(run_status, _LOGGED_STATUSES),
            run_error_code=_bounded(run_error_code, RUN_ERROR_CODES),
        )
    raise verdict


def _bind(name: str, spec: AgentStep, runtime: AgentRunner, reads: AgentStepReads) -> Step:
    agent_id = spec.agent_id

    async def step(ctx: StepContext) -> StepResult:
        if not await reads.profile_exists(agent_id):
            logger.warning(
                "agent_profile_missing", task_id=str(ctx.task_id), step=ctx.step, agent_id=agent_id
            )
            raise StepAbandoned("agent_profile_missing")
        agent_input = await spec.build_input(ctx)  # outside the try: its error is the step's bug
        run_id = uuid.uuid4()  # a new run per call, never from the checkpoint (N59)
        request = AgentRunRequest(
            agent_id=agent_id,
            task_id=ctx.task_id,
            project_id=ctx.project_id,
            agent_input=agent_input,
            cancel=ctx.cancel,
            agent_run_id=run_id,
        )
        try:
            result = await runtime.run(request)
        except AgentRunIdConflict:
            raise
        except AgentNotRunnable as error:
            logger.warning(
                "agent_step_refused",
                task_id=str(ctx.task_id),
                step=ctx.step,
                agent_id=agent_id,
                error_type=type(error).__name__,
                code="agent_unavailable",
            )
            return refused("agent_unavailable", agent_id)
        except (AgentInputInvalid, ScenarioNotAllowed) as error:
            logger.warning(
                "agent_step_refused",
                task_id=str(ctx.task_id),
                step=ctx.step,
                agent_id=agent_id,
                error_type=type(error).__name__,
                code="agent_input_invalid",
            )
            return refused("agent_input_invalid", agent_id)
        except LeaseLost:
            raise
        except Exception as error:
            logger.warning(
                "agent_step_run_unfinished",
                task_id=str(ctx.task_id),
                step=ctx.step,
                agent_id=agent_id,
                agent_run_id=str(run_id),
                error_type=type(error).__name__,
            )
            outcome = await reads.run_outcome(run_id, task_id=ctx.task_id, agent_id=agent_id)
            verdict = verdict_for_row(
                outcome.state, outcome.status, outcome.error_code, pipeline=ctx.pipeline, agent_id=agent_id
            )
            _log_ended(
                ctx,
                agent_id,
                run_id,
                source="row",
                run_status=outcome.status,
                run_error_code=outcome.error_code,
                verdict=_verdict_name(verdict),
            )
            return _deliver(ctx, verdict, agent_id, run_id, outcome.status, outcome.error_code)
        if result.agent_run_id != run_id:  # a result of another run is never trusted (N59)
            verdict = StepAbandoned("run_outcome_unknown")
            _log_ended(
                ctx,
                agent_id,
                run_id,
                source="result",
                run_status=result.status,
                run_error_code=result.error_code,
                verdict=_verdict_name(verdict),
            )
            return _deliver(ctx, verdict, agent_id, run_id, result.status, result.error_code)
        if result.status == "succeeded" and result.error_code is None and result.output is not None:
            _log_ended(
                ctx,
                agent_id,
                run_id,
                source="result",
                run_status=result.status,
                run_error_code=None,
                verdict="on_success",
            )
            return await spec.on_success(ctx, AgentSuccess(run_id, agent_id, result.output))
        verdict = verdict_for_end(result.status, result.error_code, pipeline=ctx.pipeline, agent_id=agent_id)
        _log_ended(
            ctx,
            agent_id,
            run_id,
            source="result",
            run_status=result.status,
            run_error_code=result.error_code,
            verdict=_verdict_name(verdict),
        )
        return _deliver(ctx, verdict, agent_id, run_id, result.status, result.error_code)

    step.__name__ = f"agent_step_{name}"
    return step
