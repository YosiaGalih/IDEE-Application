"""The AgentRuntime's adapters and their assembly (spec WP-3.1f Keputusan Lead #17, #21; ADR-022).

``app.agents.runtime`` only knows its ports; this module connects them to the budget guard, the usage ledger,
the lease, the provider gate, the run store and the tool gateway. The api process never imports it (the
worker builds a provider and holds the key: import-linter, tests/security/test_provider_secrets.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.runtime import BudgetRefused, ModelCall, RunScope
from app.llm.retry import AttemptHandler
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
