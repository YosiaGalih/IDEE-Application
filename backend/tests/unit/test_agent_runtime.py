"""The AgentRuntime with fake ports (plan WP-3.1f2a Tasks 3 and 5; spec WP-3.1f Keputusan Lead #15-#24).

Task 3: the single model-call path. Every attempt, the first and each retry, runs ``before_attempt``
(heartbeat -> provider gate -> budget) before anything is sent, and reports to the ledger after; a refused
hook sends nothing and records nothing; a ledger failure fails the call as ``usage_unrecorded``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.agents.runtime import BudgetRefused, LeaseLostError, ProviderBlocked, UsageUnrecorded
from app.core.cancel import CancelToken
from app.llm.base import LLMError, TextBlock
from app.llm.scenarios.model import Fail, Reply
from tests.runtime_fakes import Fakes, a_model_call, in_order, make_runtime, scripted, submit_turn

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
