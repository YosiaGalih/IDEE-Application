"""The AgentRuntime's adapters and their assembly (spec WP-3.1f Keputusan Lead #17, #21; ADR-022).

``app.agents.runtime`` only knows its ports; this module connects them to the budget guard, the usage ledger,
the lease, the provider gate, the run store and the tool gateway. The api process never imports it (the
worker builds a provider and holds the key: import-linter, tests/security/test_provider_secrets.py).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy import Integer, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.prompt_book import PromptBook
from app.agents.registry import AgentRegistry
from app.agents.runtime import (
    ERROR_TEXT_MAX,
    AgentRuntime,
    BudgetBreach,
    BudgetRefused,
    LeaseLostError,
    ModelCall,
    ProviderBlocked,
    ProviderFactory,
    RunEnd,
    RunScope,
    RunStart,
    RuntimeConfig,
    RuntimePorts,
    RunUsage,
    ToolExecutor,
)
from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.events import EventBus, emit_task_event, publish_pending
from app.core.logging import get_logger
from app.core.money import decimal_str, stored_cost
from app.core.redaction import Redactor
from app.llm.base import LLMProvider
from app.llm.factory import ProviderNotConfigured, build_provider
from app.llm.pricing import PricingTable
from app.llm.retry import AttemptHandler
from app.models.agents import AgentRun
from app.models.projects import Project
from app.models.providers import ProviderConfig
from app.services.provider_readiness import (
    PROVIDER,
    WorkerCredentials,
    effective_models,
    provider_calls_allowed,
    provider_ready,
)
from app.services.tool_gateway import build_tool_gateway
from app.services.usage import (
    EMPTY_TOTALS,
    UsageCallContext,
    UsageLedger,
    incomplete_run_totals,
    run_usage,
    task_usage,
)
from app.workflow.budget import BudgetDefaults, BudgetExceeded, BudgetGuard, CallBound
from app.workflow.lease import LeaseLost, renew_lease

# Every port that writes the task row gives up on a lock and on a statement (reviews of 42bcb2e; as
# claim_lease does): a lock held by another transaction never stalls a run.
_log = get_logger(__name__)
LOCK_TIMEOUT: Final = "5s"
STATEMENT_TIMEOUT: Final = "10s"


async def _bounded(session: AsyncSession) -> None:
    """``SET LOCAL lock_timeout`` and ``statement_timeout`` for the transaction that starts here."""
    await session.execute(
        select(
            func.set_config("lock_timeout", LOCK_TIMEOUT, True),
            func.set_config("statement_timeout", STATEMENT_TIMEOUT, True),
        )
    )


@dataclass(frozen=True, slots=True)
class BoundCall:
    bound: CallBound
    ledger: UsageCallContext


def bind_model_call(call: ModelCall) -> BoundCall:
    """The single constructor of the budget bound and the ledger context (Keputusan Lead #17): both come
    from the same ``ModelCall``, so what the budget checks is what the ledger records."""
    scope = call.scope
    return BoundCall(
        bound=CallBound(
            model=call.model, input_token_bound=call.input_token_bound, output_token_cap=call.output_token_cap
        ),
        ledger=UsageCallContext(
            project_id=scope.project_id,
            call_id=call.call_id,
            input_token_bound=call.input_token_bound,
            output_token_cap=call.output_token_cap,
            task_id=scope.task_id,
            agent_run_id=scope.agent_run_id,
            agent_id=scope.agent_id,
        ),
    )


def _refused(exc: BudgetExceeded) -> BudgetRefused:
    return BudgetRefused(exc.kind, exc.limit, exc.used, next_bound=exc.next_bound)


class GuardBudget:
    """``BudgetCheck`` over ``BudgetGuard``."""

    def __init__(self, guard: BudgetGuard) -> None:
        self._guard = guard

    async def before_model_attempt(self, call: ModelCall) -> None:
        try:
            await self._guard.check_before_attempt(call.scope.task_id, bind_model_call(call).bound)
        except BudgetExceeded as exc:
            raise _refused(exc) from exc

    async def before_tool(self, scope: RunScope) -> None:
        try:
            await self._guard.check_before_tool(scope.task_id)
        except BudgetExceeded as exc:
            raise _refused(exc) from exc


class LedgerRecorder:
    """``UsageLedgerPort`` over ``UsageLedger``."""

    def __init__(self, ledger: UsageLedger) -> None:
        self._ledger = ledger

    def on_attempt(self, call: ModelCall) -> AttemptHandler:
        return self._ledger.recorder(bind_model_call(call).ledger)


class DbProviderGate:
    """``ProviderGate``: the provider readiness gate and the project's kill switch (Keputusan Lead #16)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        """Holds WorkerCredentials(settings.anthropic_api_key, settings.anthropic_base_url): the worker's own
        key and endpoint, read once (the process env). The api process never imports this module."""
        self._sessions = sessions
        self._settings = settings
        self._held = WorkerCredentials(settings.anthropic_api_key, settings.anthropic_base_url)

    async def check(self, project_id: uuid.UUID) -> Mapping[str, str]:
        """One short read-only session per call: a switch flipped mid-run counts on the next attempt.
        ``provider_ready`` first ('disabled' -> PROVIDER_DISABLED; 'not_ready'/'unknown' ->
        provider_not_ready), then the kill switch (anything but true -> PROVIDER_DISABLED). A missing project
        fails closed. Returns the effective model per tier."""
        async with self._sessions() as session:
            row = (
                await session.scalars(select(ProviderConfig).where(ProviderConfig.provider == PROVIDER))
            ).one_or_none()
            project = (await session.scalars(select(Project).where(Project.id == project_id))).one_or_none()
        verdict = provider_ready(row, self._settings, held=self._held)
        if verdict.state == "disabled":
            raise ProviderBlocked("PROVIDER_DISABLED", verdict.reason)
        if not verdict.ready:
            raise ProviderBlocked("provider_not_ready", verdict.reason)
        if project is None:
            raise ProviderBlocked("PROVIDER_DISABLED", "project_missing")
        if not provider_calls_allowed(project):
            raise ProviderBlocked("PROVIDER_DISABLED", "kill_switch")
        return effective_models(self._settings, (row.model_policy or {}) if row is not None else {})


class LeaseHeartbeat:
    """``Heartbeat`` over the task lease (Keputusan Lead #18, #33)."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], worker_id: str) -> None:
        self._sessions = sessions
        self._worker_id = worker_id

    async def beat(self, task_id: uuid.UUID) -> None:
        """``renew_lease`` in its own transaction, committed at once (never held across a tool or a model
        call); ``LeaseLost`` -> ``LeaseLostError``."""
        try:
            async with self._sessions.begin() as session:
                await _bounded(session)
                await renew_lease(session, task_id, self._worker_id)
        except LeaseLost as exc:
            raise LeaseLostError(str(exc)) from exc


class TaskRunEvents:
    """``RunEvents``: run events of a task (API_CONTRACTS §5.1)."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], bus: EventBus | None, redactor: Redactor
    ) -> None:
        self._sessions = sessions
        self._bus = bus
        self._redactor = redactor

    async def delta(self, scope: RunScope, text: str, *, reset: bool = False) -> None:
        """``agent.delta``, ephemeral; skipped without a bus (never stored). With ``reset`` the client
        replaces its text with ``text`` instead of appending it."""
        if self._bus is None:
            return
        async with self._sessions() as session:  # unused for an ephemeral event: no connection is opened
            await emit_task_event(
                session,
                self._bus,
                scope.task_id,
                "agent.delta",
                {"agent_run_id": str(scope.agent_run_id), "text": text, "reset": reset},
                redactor=self._redactor,
                ephemeral=True,
            )

    async def usage_updated(self, scope: RunScope) -> None:
        """``usage.updated`` with the task's totals from the ledger, money as decimal strings; its own
        transaction, committed, then published."""
        async with self._sessions() as session:
            await _bounded(session)
            totals = (await task_usage(session, [scope.task_id])).get(scope.task_id, EMPTY_TOTALS)
            cost = totals.estimated_cost
            await emit_task_event(
                session,
                self._bus,
                scope.task_id,
                "usage.updated",
                {
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "estimated_cost": decimal_str(cost) if cost is not None else None,
                    "cost_status": totals.cost_status,
                },
                redactor=self._redactor,
            )
            await session.commit()
            if self._bus is not None:
                await publish_pending(session, self._bus)


class AgentRunAlreadyFinished(LookupError):
    """The run is not ``running`` any more: a run is finished once (never overwritten)."""


def _number(value: int | Decimal | None) -> int | str | None:
    """A budget figure for an event or the audit: money as a decimal string, counts as they are."""
    return decimal_str(value) if isinstance(value, Decimal) else value


class AgentRunWriter:
    """``RunStore``: the only writer of ``agent_runs`` in 3.1f2a (tests/unit/test_agent_run_writer_guard.py;
    recovery joins in 3.1f2b). Everything stored goes through the audit's redactor, which knows the
    configured secret literals (3.1b C4)."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], bus: EventBus | None, audit: AuditWriter
    ) -> None:
        self._sessions = sessions
        self._bus = bus
        self._audit = audit
        self._projects: dict[uuid.UUID, uuid.UUID] = {}  # run -> project, for the audit of its end

    async def start(self, run: RunStart) -> uuid.UUID:
        """INSERT ``agent_runs`` (``running``, the input redacted) and ``agent.run_started``, one transaction,
        committed, then published."""
        redactor = self._audit.redactor
        run_id = uuid.uuid4()
        async with self._sessions() as session:
            await _bounded(session)
            session.add(
                AgentRun(
                    id=run_id,
                    task_id=run.task_id,
                    agent_id=run.agent_id,
                    agent_version=run.agent_version,
                    step=run.step,
                    model_provider=run.model_provider,
                    model_name=run.model_name,
                    prompt_version=run.prompt_version,
                    status="running",
                    input=redactor.redact_obj(dict(run.input)),
                )
            )
            await session.flush()
            await emit_task_event(
                session,
                self._bus,
                run.task_id,
                "agent.run_started",
                {"agent_run_id": str(run_id), "agent_id": run.agent_id, "status": "running"},
                redactor=redactor,
            )
            await session.commit()
            if self._bus is not None:
                await publish_pending(session, self._bus)
        self._projects[run_id] = run.project_id
        return run_id

    async def finish(self, agent_run_id: uuid.UUID, end: RunEnd) -> RunUsage:
        """Totals from the run's ledger rows; UPDATE status, code, redacted error (cut) and output,
        iterations, tokens, cost, ``finished_at`` (DB clock) and ``duration_ms``; ``agent.run_finished``;
        for ``budget_exceeded`` also ``usage.budget_exceeded`` and the audit ``budget.exceeded``. One
        transaction. Only a row still ``running`` is finished (``AgentRunAlreadyFinished``)."""
        redactor = self._audit.redactor
        async with self._sessions() as session:
            await _bounded(session)
            run_totals = await run_usage(session, agent_run_id)
            if not end.usage_complete or end.error_code == "usage_unrecorded":
                run_totals = incomplete_run_totals(run_totals)
            totals = run_totals.totals
            cost = stored_cost(totals.estimated_cost) if totals.estimated_cost is not None else None
            finished = (
                await session.execute(
                    update(AgentRun)
                    .where(AgentRun.id == agent_run_id, AgentRun.status == "running")
                    .values(
                        status=end.status,
                        error_code=end.error_code,
                        error=redactor.redact(end.error)[:ERROR_TEXT_MAX] if end.error is not None else None,
                        output=redactor.redact_obj(dict(end.output)) if end.output is not None else None,
                        iterations=end.iterations,
                        input_tokens=totals.input_tokens,
                        output_tokens=totals.output_tokens,
                        cache_read_tokens=run_totals.cache_read_tokens,
                        cache_creation_tokens=run_totals.cache_creation_tokens,
                        estimated_cost=cost,
                        cost_status=totals.cost_status,
                        finished_at=func.now(),
                        duration_ms=cast(
                            func.extract("epoch", func.now() - AgentRun.started_at) * 1000, Integer
                        ),
                    )
                    .returning(AgentRun.task_id, AgentRun.agent_id)
                    .execution_options(synchronize_session=False)
                )
            ).one_or_none()
            if finished is None:
                self._projects.pop(agent_run_id, None)  # finished elsewhere: nothing more to audit
                raise AgentRunAlreadyFinished(f"agent run {agent_run_id} is not running")
            task_id, agent_id = finished
            usage = {
                "input_tokens": totals.input_tokens,
                "output_tokens": totals.output_tokens,
                "estimated_cost": decimal_str(cost) if cost is not None else None,
                "cost_status": totals.cost_status,
            }
            await emit_task_event(
                session,
                self._bus,
                task_id,
                "agent.run_finished",
                {
                    "agent_run_id": str(agent_run_id),
                    "agent_id": agent_id,
                    "status": end.status,
                    "error_code": end.error_code,
                    "usage": usage,
                },
                redactor=redactor,
            )
            if end.budget is not None:
                await self._budget_exceeded(session, agent_run_id, task_id, agent_id, end.budget)
            await session.commit()
            if self._bus is not None:
                await publish_pending(session, self._bus)
        self._projects.pop(agent_run_id, None)
        return RunUsage(
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cache_read_tokens=run_totals.cache_read_tokens,
            cache_creation_tokens=run_totals.cache_creation_tokens,
            estimated_cost=cost,
            cost_status=totals.cost_status,
        )

    async def fail(self, agent_run_id: uuid.UUID) -> None:
        """The minimal end when ``finish`` could not be written (review M5): ``failed internal_error``,
        nothing else, only for a row still ``running``."""
        self._projects.pop(agent_run_id, None)
        async with self._sessions() as session:
            await _bounded(session)
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id == agent_run_id, AgentRun.status == "running")
                .values(
                    status="failed",
                    error_code="internal_error",
                    error="the end of the run could not be recorded",
                    finished_at=func.now(),
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()

    async def _budget_exceeded(
        self,
        session: AsyncSession,
        agent_run_id: uuid.UUID,
        task_id: uuid.UUID,
        agent_id: str,
        breach: BudgetBreach,
    ) -> None:
        await emit_task_event(
            session,
            self._bus,
            task_id,
            "usage.budget_exceeded",
            {"kind": breach.kind, "limit": _number(breach.limit), "used": _number(breach.used)},
            redactor=self._audit.redactor,
        )
        await self._audit.write(
            session,
            actor_type="agent",
            actor_id=agent_id,
            action="budget.exceeded",
            result="denied",
            resource_type="agent_run",
            resource_id=str(agent_run_id),
            project_id=self._projects.get(agent_run_id),
            metadata={
                "task_id": str(task_id),
                "kind": breach.kind,
                "limit": _number(breach.limit),
                "used": _number(breach.used),
                "next_bound": _number(breach.next_bound),
            },
        )


def build_agent_runtime(
    *,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    registry: AgentRegistry,
    pricing: PricingTable,
    audit: AuditWriter,
    bus: EventBus | None,
    worker_id: str,
    tools: ToolExecutor | None = None,
    providers: ProviderFactory | None = None,
    prompts: PromptBook | None = None,
) -> AgentRuntime:
    """The worker's AgentRuntime: every port over the database, the production tool gateway, one redactor
    (the audit's, with the configured secret literals) for everything the runtime writes or sends."""
    redactor = audit.redactor
    ports = RuntimePorts(
        gate=DbProviderGate(sessions, settings),
        budget=GuardBudget(BudgetGuard(sessions, pricing, BudgetDefaults.from_settings(settings))),
        ledger=LedgerRecorder(UsageLedger(sessions, pricing)),
        runs=AgentRunWriter(sessions, bus, audit),
        events=TaskRunEvents(sessions, bus, redactor),
        tools=tools if tools is not None else build_tool_gateway(sessions=sessions, audit=audit, bus=bus),
        heartbeat=LeaseHeartbeat(sessions, worker_id),
        providers=providers if providers is not None else SettingsProviderFactory(settings),
    )
    return AgentRuntime(
        registry=registry,
        prompts=prompts if prompts is not None else PromptBook.load(),
        ports=ports,
        config=RuntimeConfig(
            max_tool_iterations=settings.claude_max_tool_iterations,
            max_output_tokens=settings.claude_max_output_tokens,
            request_timeout_s=float(settings.claude_request_timeout_seconds),
            scripted=settings.llm_provider == "scripted",
        ),
        redactor=redactor,
    )


class SettingsProviderFactory:
    """``ProviderFactory``: one provider per run, built from the worker's settings and closed after the run
    (Keputusan Lead #28; syarat 3.1a #6)."""

    def __init__(
        self, settings: Settings, *, build: Callable[[Settings], LLMProvider] = build_provider
    ) -> None:
        self._settings = settings
        self._build = build

    @asynccontextmanager
    async def open(self) -> AsyncIterator[LLMProvider]:
        """``ProviderNotConfigured`` (no key in this process) -> ``ProviderBlocked('provider_not_ready',
        'no_provider')``; ``aclose()`` in ``finally``, also when the run fails or is cancelled."""
        try:
            provider = self._build(self._settings)
        except ProviderNotConfigured as exc:
            raise ProviderBlocked("provider_not_ready", "no_provider") from exc
        try:
            yield provider
        finally:
            try:
                await provider.aclose()
            except Exception as error:  # never discards the run's result (review L3)
                _log.warning(
                    "llm_provider_close_failed", provider=provider.name, error_type=type(error).__name__
                )
