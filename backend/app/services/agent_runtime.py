"""The AgentRuntime's adapters and their assembly (spec WP-3.1f Keputusan Lead #17, #21; ADR-022).

``app.agents.runtime`` only knows its ports; this module connects them to the budget guard, the usage ledger,
the lease, the provider gate, the run store and the tool gateway. The api process never imports it (the
worker builds a provider and holds the key: import-linter, tests/security/test_provider_secrets.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.runtime import BudgetRefused, LeaseLostError, ModelCall, ProviderBlocked, RunScope
from app.core.config import Settings
from app.core.events import EventBus, emit_task_event, publish_pending
from app.core.money import decimal_str
from app.core.redaction import Redactor
from app.llm.retry import AttemptHandler
from app.models.projects import Project
from app.models.providers import ProviderConfig
from app.services.provider_readiness import (
    PROVIDER,
    WorkerCredentials,
    effective_models,
    provider_calls_allowed,
    provider_ready,
)
from app.services.usage import EMPTY_TOTALS, UsageCallContext, UsageLedger, task_usage
from app.workflow.budget import BudgetExceeded, BudgetGuard, CallBound
from app.workflow.lease import LeaseLost, renew_lease


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

    async def delta(self, scope: RunScope, text: str) -> None:
        """``agent.delta``, ephemeral; skipped without a bus (never stored)."""
        if self._bus is None:
            return
        async with self._sessions() as session:  # unused for an ephemeral event: no connection is opened
            await emit_task_event(
                session,
                self._bus,
                scope.task_id,
                "agent.delta",
                {"agent_run_id": str(scope.agent_run_id), "text": text},
                redactor=self._redactor,
                ephemeral=True,
            )

    async def usage_updated(self, scope: RunScope) -> None:
        """``usage.updated`` with the task's totals from the ledger, money as decimal strings; its own
        transaction, committed, then published."""
        async with self._sessions() as session:
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
