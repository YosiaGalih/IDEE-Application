"""The AgentRuntime's adapters and their assembly (spec WP-3.1f Keputusan Lead #17, #21; ADR-022).

``app.agents.runtime`` only knows its ports; this module connects them to the budget guard, the usage ledger,
the lease, the provider gate, the run store and the tool gateway. The api process never imports it (the
worker builds a provider and holds the key: import-linter, tests/security/test_provider_secrets.py).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.prompt_book import PromptBook
from app.agents.registry import AgentRegistry
from app.agents.runtime import (
    AgentRunIdConflict,
    AgentRuntime,
    BudgetBreach,
    BudgetRefused,
    GateResult,
    LeaseLostError,
    LogContext,
    ModelCall,
    ProviderBlocked,
    ProviderFactory,
    RejectCause,
    RunAlreadyFinished,
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
from app.core.db import bound_transaction
from app.core.events import EventBus, emit_task_event, publish_pending, task_project_id
from app.core.logging import get_logger
from app.core.money import decimal_str
from app.core.redaction import Redactor
from app.llm.base import LLMProvider
from app.llm.factory import ProviderNotConfigured, build_provider
from app.llm.pricing import PricingTable
from app.llm.retry import AttemptHandler
from app.models.agents import AgentRun
from app.models.projects import Project
from app.models.providers import ProviderConfig
from app.services.agent_run_rows import UNKNOWN_RUN_TOTALS, RowOwner, close_run_row
from app.services.provider_readiness import (
    PROVIDER,
    WorkerCredentials,
    effective_models,
    invalidate_report,
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

_log = get_logger(__name__)


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

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        audit: AuditWriter,
        pricing_version: str | None,
    ) -> None:
        """Holds WorkerCredentials(settings.anthropic_api_key, settings.anthropic_base_url, pricing_version):
        the worker's own key, endpoint and tariff version, read once (the process env and the table loaded at
        start; ``None``: no tariff is checked). ``audit`` is the worker's, with the configured secret literals
        (backend M2): both are required, so no caller gets a default by forgetting them. The api process never
        imports this module."""
        self._sessions = sessions
        self._settings = settings
        self._held = WorkerCredentials(
            settings.anthropic_api_key, settings.anthropic_base_url, pricing_version
        )
        self._audit = audit

    async def check(self, project_id: uuid.UUID) -> GateResult:
        """One short read-only session per call: a switch flipped mid-run counts on the next attempt.
        ``provider_ready`` first ('disabled' -> PROVIDER_DISABLED; 'not_ready'/'unknown' ->
        provider_not_ready), then the kill switch (anything but true -> PROVIDER_DISABLED). A missing project
        fails closed. A ``pricing_changed`` the worker found itself is recorded on the report first (ruling
        R2). Returns the effective model per tier and the timestamp of the report the verdict relied on: none
        under ``readiness_not_required`` (silent L2), so a reject resets no report it ignored."""
        async with self._sessions() as session:
            row = (
                await session.scalars(select(ProviderConfig).where(ProviderConfig.provider == PROVIDER))
            ).one_or_none()
            project = (await session.scalars(select(Project).where(Project.id == project_id))).one_or_none()
        verdict = provider_ready(row, self._settings, held=self._held)
        if (
            verdict.reason == "pricing_changed"
            and row is not None
            and "invalidated" not in (row.readiness_report or {})
        ):
            await self._record_tariff_refusal(project_id, row.readiness_checked_at)
        if verdict.state == "disabled":
            raise ProviderBlocked("PROVIDER_DISABLED", verdict.reason)
        if not verdict.ready:
            raise ProviderBlocked("provider_not_ready", verdict.reason)
        if project is None:
            raise ProviderBlocked("PROVIDER_DISABLED", "project_missing")
        if not provider_calls_allowed(project):
            raise ProviderBlocked("PROVIDER_DISABLED", "kill_switch")
        models = effective_models(self._settings, (row.model_policy or {}) if row is not None else {})
        report_at = row.readiness_checked_at if row is not None else None
        if verdict.reason == "readiness_not_required":
            report_at = None  # the gate relied on no report: a reject resets none
        return GateResult(models, report_at)

    async def _record_tariff_refusal(self, project_id: uuid.UUID, report_at: datetime | None) -> None:
        """Ruling R2 (silent H1): the worker refuses the report's tariff, so the report is marked
        ``invalidated`` with cause ``pricing_changed`` and the api verdict, which loads no tariff, shows it
        too. The same compare-and-set, once-per-report guard and audit as a rejection, in its own short
        transaction, naming the checked project (no run correlation). Best-effort: a failure is logged (ids
        and type only) and the gate refuses anyway."""
        try:
            async with self._sessions.begin() as session:
                await bound_transaction(session)
                await invalidate_report(
                    session,
                    cause="pricing_changed",
                    report_at=report_at,
                    audit=self._audit,
                    project_id=project_id,
                    correlation_id=None,
                )
        except Exception as error:
            _log.warning(
                "provider_reset_unrecorded",
                project_id=str(project_id),
                cause="pricing_changed",
                error_type=type(error).__name__,
            )

    async def reject(self, cause: RejectCause, *, report_at: datetime | None, scope: RunScope) -> None:
        """Best-effort (the runtime swallows a failure): reset the report this run judged, in its own
        transaction, if it is still the current one (``invalidate_report``). The audit row names the run's
        project and correlation id (AG-13 LOW-3)."""
        async with self._sessions.begin() as session:
            await bound_transaction(session)
            await invalidate_report(
                session,
                cause=cause,
                report_at=report_at,
                audit=self._audit,
                project_id=scope.project_id,
                correlation_id=f"agent_run:{scope.agent_run_id}",
            )


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
                await bound_transaction(session)
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

    async def delta(self, scope: RunScope, text: str, *, call: int, reset: bool = False) -> None:
        """``agent.delta``, ephemeral; skipped without a bus (never stored). ``call`` is the run's model call,
        from 1; with ``reset`` the client replaces that call's text with ``text`` instead of appending it
        (Lead decision 3.1f2c #9)."""
        if self._bus is None:
            return
        async with self._sessions() as session:  # unused for an ephemeral event: no connection is opened
            await emit_task_event(
                session,
                self._bus,
                scope.task_id,
                "agent.delta",
                {"agent_run_id": str(scope.agent_run_id), "call": call, "text": text, "reset": reset},
                redactor=self._redactor,
                ephemeral=True,
            )

    async def usage_updated(self, scope: RunScope) -> None:
        """``usage.updated`` with the task's totals from the ledger, money as decimal strings; its own
        transaction, committed, then published."""
        async with self._sessions() as session:
            await bound_transaction(session)
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


class AgentRunAlreadyFinished(RunAlreadyFinished):
    """The run is not ``running`` any more: a run is finished once (never overwritten), and its id is never
    started again (spent; ruling R5). The runtime knows it as the port's ``RunAlreadyFinished``."""


def _number(value: int | Decimal | None) -> int | str | None:
    """A budget figure for an event or the audit: money as a decimal string, counts as they are."""
    return decimal_str(value) if isinstance(value, Decimal) else value


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What the ``agent_runs`` row says about a run: ``missing`` (no row was written), ``running`` (belongs to
    recovery), ``paused`` (``approval_required``: waiting for a human decision, not an end) or ``finished``
    with its status and code."""

    state: Literal["finished", "paused", "running", "missing"]
    status: str | None
    error_code: str | None


async def read_run_outcome(
    session: AsyncSession, agent_run_id: uuid.UUID, *, task_id: uuid.UUID, agent_id: str
) -> RunOutcome:
    """The row's end, read only. For a caller whose ``AgentRuntime.run`` returned no result (Keputusan Lead
    #11); not for ``AgentRunIdConflict``, whose row is another run's. Only the row of this id, ``task_id`` and
    ``agent_id`` is read: a row of another task or agent is ``missing`` (rulings R6, G2)."""
    row = (
        await session.execute(
            select(AgentRun.status, AgentRun.error_code).where(
                AgentRun.id == agent_run_id, AgentRun.task_id == task_id, AgentRun.agent_id == agent_id
            )
        )
    ).one_or_none()
    if row is None:
        return RunOutcome("missing", None, None)
    status, error_code = row
    if status == "running":
        return RunOutcome("running", status, error_code)
    if status == "approval_required":  # a pause for a human decision: the run has not ended (silent L1)
        return RunOutcome("paused", status, error_code)
    return RunOutcome("finished", status, error_code)


class AgentRunWriter:
    """``RunStore``: the only creator of ``agent_runs`` rows (``start``'s INSERT); ``finish`` and ``fail``
    close the row of their run's task, agent and start token only, and only through
    ``app.services.agent_run_rows.close_run_row``, which the run recovery also uses
    (tests/unit/test_agent_run_writer_guard.py, tests/unit/test_close_run_row_guard.py). Everything stored
    goes through the audit's redactor, which knows the configured secret literals (3.1b C4)."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], bus: EventBus | None, audit: AuditWriter
    ) -> None:
        self._sessions = sessions
        self._bus = bus
        self._audit = audit
        self._projects: dict[uuid.UUID, uuid.UUID] = {}  # run -> project, for the audit of its end

    async def start(self, run: RunStart, *, retry: bool) -> None:
        """INSERT ``agent_runs`` (``running``, the input redacted, the run's start token) and
        ``agent.run_started``, one transaction, committed, then published. A first start (``retry=False``)
        whose INSERT creates no row is refused with ``AgentRunIdConflict``, whatever that row holds, and the
        row is not read: a duplicate request never takes over a live run's row (ruling R10). A retry after a
        lost ack (``retry=True``) whose INSERT creates the row stores the token too. One whose row exists
        checks identity first (spec WP-3.2 §16.3a #2): unless task, agent **and** start token all match
        (a None token, the row's or the run's, never does), ``AgentRunIdConflict``, whatever the row's
        status, so a duplicate request that committed the row while this one's first start had failed before
        its INSERT is never adopted (AG-13 delta-2 LOW-1 of 3.1f2b1; Keputusan Lead #66). Then the status: a
        run that has ended is ``AgentRunAlreadyFinished`` (the id is spent: ``start`` never reopens a run;
        ruling R5), a ``running`` one is left as it is (no second event). The token is compared, never logged,
        emitted or put in an exception's text."""
        redactor = self._audit.redactor
        run_id = run.agent_run_id
        async with self._sessions() as session:
            await bound_transaction(session)
            inserted = (
                await session.execute(
                    pg_insert(AgentRun)
                    .values(
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
                        start_token=run.start_token,
                    )
                    .on_conflict_do_nothing(index_elements=[AgentRun.id])
                    .returning(AgentRun.id)
                )
            ).scalar_one_or_none()
            if inserted is None:
                if not retry:  # nothing was written: the id is another request's, live or ended (R10)
                    raise AgentRunIdConflict(f"agent run id {run_id} is already used")
                existing = (
                    await session.execute(
                        select(
                            AgentRun.task_id, AgentRun.agent_id, AgentRun.start_token, AgentRun.status
                        ).where(AgentRun.id == run_id)
                    )
                ).one()
                # Identity before status (spec WP-3.2 §16.3a #2): another request's ended run is never read
                # back as this one's end. A None token on either side is never an identity, whatever static
                # typing says (Lead ruling R-F7): checked before the compare, where None would equal None.
                if existing.start_token is None or run.start_token is None:
                    raise AgentRunIdConflict(
                        f"agent run id {run_id} belongs to another task, agent or request"
                    )
                if (existing.task_id, existing.agent_id, existing.start_token) != (
                    run.task_id,
                    run.agent_id,
                    run.start_token,
                ):
                    raise AgentRunIdConflict(
                        f"agent run id {run_id} belongs to another task, agent or request"
                    )
                if existing.status != "running":
                    raise AgentRunAlreadyFinished(f"agent run {run_id} has ended: its id is spent")
                self._projects[run_id] = run.project_id  # its end is audited like a first start's
                return
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

    async def finish(
        self,
        agent_run_id: uuid.UUID,
        end: RunEnd,
        *,
        task_id: uuid.UUID,
        agent_id: str,
        start_token: uuid.UUID,
    ) -> RunUsage:
        """Totals from the run's ledger rows (known to be incomplete when ``end`` says so); the row closed by
        ``close_run_row`` with status, code, redacted error (cut) and output, iterations, tokens, cost,
        ``finished_at`` (DB clock) and ``duration_ms``, and ``agent.run_finished``; for ``budget_exceeded``
        also ``usage.budget_exceeded`` and the audit ``budget.exceeded``. One transaction. Only a row still
        ``running`` row of this id, task, agent **and start token** is finished (``AgentRunAlreadyFinished``
        for any other: a duplicate request's row of the same id, another task's or agent's, one with no
        token; spec WP-3.2 §16.6.2 #88). The token is compared, never logged, emitted or put in an
        exception's text."""
        redactor = self._audit.redactor
        async with self._sessions() as session:
            await bound_transaction(session)
            run_totals = await run_usage(session, agent_run_id)
            if not end.usage_complete or end.error_code == "usage_unrecorded":
                run_totals = incomplete_run_totals(run_totals)
            closed = await close_run_row(
                session,
                self._bus,
                redactor,
                agent_run_id,
                status=end.status,
                error_code=end.error_code,
                error=end.error,
                totals=run_totals,
                output=end.output,
                iterations=end.iterations,
                owner=RowOwner(task_id, agent_id, start_token),
            )
            if closed is None:
                self._projects.pop(agent_run_id, None)  # finished elsewhere: nothing more to audit
                raise AgentRunAlreadyFinished(f"agent run {agent_run_id} is not running")
            if end.budget is not None:
                await self._budget_exceeded(
                    session,
                    agent_run_id,
                    closed.task_id,
                    closed.agent_id,
                    self._projects.get(agent_run_id),
                    end.budget,
                )
            await session.commit()
            if self._bus is not None:
                await publish_pending(session, self._bus)
        self._projects.pop(agent_run_id, None)
        totals = run_totals.totals
        return RunUsage(
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cache_read_tokens=run_totals.cache_read_tokens,
            cache_creation_tokens=run_totals.cache_creation_tokens,
            estimated_cost=closed.estimated_cost,
            cost_status=totals.cost_status,
        )

    async def fail(
        self,
        agent_run_id: uuid.UUID,
        *,
        task_id: uuid.UUID,
        agent_id: str,
        start_token: uuid.UUID,
        breach: BudgetBreach | None = None,
    ) -> None:
        """The minimal end when ``finish`` could not be written (review M5): ``failed internal_error`` with
        its usage unknown and no ``duration_ms``, only for the row of this id, task, agent and start token
        that is still ``running`` (a foreign id ends nothing, ruling R6; nor a duplicate request's row of the
        same id, nor a row with no token, spec WP-3.2 §16.3a #1); then, in the same transaction,
        ``agent.run_finished`` in ``finish``'s envelope (review N6 of 073e68d): both by ``close_run_row``.
        When no row matched, nothing is written and ``agent_run_not_marked`` is logged with the ids only. A
        known ``breach`` also writes ``usage.budget_exceeded`` and the audit ``budget.exceeded`` (D-I5); the
        status stays ``failed internal_error``. Those two are written in a savepoint (ruling R7): when they
        fail, the savepoint alone is rolled back, ``budget_audit_unrecorded`` is logged with the run's ids and
        the error type, and the minimal end is still committed."""
        project_id = self._projects.pop(agent_run_id, None)
        async with self._sessions() as session:
            await bound_transaction(session)
            closed = await close_run_row(
                session,
                self._bus,
                self._audit.redactor,
                agent_run_id,
                status="failed",
                error_code="internal_error",
                error="the end of the run could not be recorded",
                totals=UNKNOWN_RUN_TOTALS,
                with_duration=False,  # NULL: the agents API's avg_duration_ms counts measured runs only
                owner=RowOwner(task_id, agent_id, start_token),
            )
            if closed is None:  # finished elsewhere (its end was already published), or not this run's row
                _log.warning(  # ids only, never the token (silent L1)
                    "agent_run_not_marked",
                    agent_run_id=str(agent_run_id),
                    task_id=str(task_id),
                    agent_id=agent_id,
                    breach_kind=breach.kind if breach else None,
                )
                return
            if breach is not None:
                try:
                    async with session.begin_nested():
                        await self._budget_exceeded(
                            session, agent_run_id, task_id, agent_id, project_id, breach
                        )
                except (
                    Exception
                ) as error:  # the end is the record; the audit of its breach is best-effort here
                    _log.error(
                        "budget_audit_unrecorded",
                        agent_run_id=str(agent_run_id),
                        task_id=str(task_id),
                        breach_kind=breach.kind,
                        error_type=type(error).__name__,
                    )
            await session.commit()
            if self._bus is not None:
                await publish_pending(session, self._bus)

    async def _budget_exceeded(
        self,
        session: AsyncSession,
        agent_run_id: uuid.UUID,
        task_id: uuid.UUID,
        agent_id: str,
        cached_project: uuid.UUID | None,
        breach: BudgetBreach,
    ) -> None:
        """``usage.budget_exceeded`` and the audit ``budget.exceeded``, for ``finish`` and ``fail`` alike. The
        audit's project is the one ``start`` cached, else the task's; when neither is known the audit is still
        written, without a project, and ``budget_audit_project_missing`` is logged."""
        project_id = cached_project if cached_project is not None else await task_project_id(session, task_id)
        if project_id is None:
            _log.error("budget_audit_project_missing", agent_run_id=str(agent_run_id), task_id=str(task_id))
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
            project_id=project_id,
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
        gate=DbProviderGate(sessions, settings, audit=audit, pricing_version=pricing.version),
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
    async def open(self, log_context: LogContext) -> AsyncIterator[LLMProvider]:
        """``ProviderNotConfigured`` (no key in this process) -> ``ProviderBlocked('provider_not_ready',
        'no_provider')``; ``aclose()`` in ``finally``, also when the run fails or is cancelled. A failed close
        is logged with the run's ids from ``log_context``."""
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
                    "llm_provider_close_failed",
                    **log_context(),
                    provider=provider.name,
                    error_type=type(error).__name__,
                )
