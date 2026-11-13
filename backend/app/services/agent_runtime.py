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

from app.agents.runtime import BudgetRefused, ModelCall, ProviderBlocked, RunScope
from app.core.config import Settings
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
from app.services.usage import UsageCallContext, UsageLedger
from app.workflow.budget import BudgetExceeded, BudgetGuard, CallBound


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
        raise NotImplementedError  # plan WP-3.1f2a Task 5


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
