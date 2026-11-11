"""BudgetGuard's pure rules (SECURITY_MODEL T4; spec WP-3.1f Keputusan Lead #2, #6; plan 3.1f1 Task 4 and
Keputusan plan #3 to #5): effective caps, conservative accounting per ledger row, worst-case prices, and the
``used + bound(next)`` checks."""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.config import load_settings
from app.llm.base import ERROR_KINDS
from app.llm.pricing import PricingTable, load_pricing
from app.models.enums import TOOL_CALL_STATUSES
from app.workflow.budget import (
    BUDGET_KEYS,
    REJECTED_BEFORE_PROCESSING,
    UNCOUNTED_TOOL_STATUSES,
    BudgetConfigError,
    BudgetDefaults,
    BudgetExceeded,
    BudgetFields,
    BudgetUsage,
    CallBound,
    Charge,
    EffectiveBudget,
    LedgerRow,
    PriceCeiling,
    bound_charge,
    charge,
    dump_budget,
    effective_budget,
    evaluate,
    parse_budget,
    price_ceiling,
)
from tests.conftest import make_test_env

# The dearest rate of each kind sits in a different model or tier, so a ceiling taken from the first, the
# cheapest or the base tier only is visibly wrong.
TABLE = """
version: "2026-11-05"
currency: USD
source: https://example.test/pricing
verify_before_production: true
models:
  claude-cheap:
    input: "1"
    output: "5"
    cache_write: "1.25"
    cache_read: "0.10"
  claude-tiered:
    input: "2"
    output: "10"
    cache_write: "2.50"
    cache_read: "0.20"
    long_prompt:
      above_tokens: 100000
      input: "4"
      output: "20"
      cache_write: "5"
      cache_read: "0.40"
  claude-dear-output:
    input: "3"
    output: "30"
    cache_write: "2.50"
    cache_read: "0.30"
"""
EMPTY_TABLE = TABLE.split("models:")[0] + "models: {}\n"
MILLION = Decimal(1_000_000)
DEFAULTS = BudgetDefaults(max_cost_usd=Decimal("2"), max_tokens=400_000, max_duration_minutes=60)


def _load(tmp_path: Path, content: str) -> PricingTable:
    path = tmp_path / "pricing.yaml"
    path.write_text(content, encoding="utf-8")
    return load_pricing(path)


@pytest.fixture
def table(tmp_path: Path) -> PricingTable:
    return _load(tmp_path, TABLE)


@pytest.fixture
def empty_table(tmp_path: Path) -> PricingTable:
    return _load(tmp_path, EMPTY_TABLE)


def row(**overrides: object) -> LedgerRow:
    values: dict[str, object] = {
        "status": "ok",
        "error_kind": None,
        "model": "claude-dear-output",
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_creation_tokens": 10,
        "cache_read_tokens": 1_000,
        "estimated_cost": Decimal("0.0012"),
        "input_token_bound": 1_000,
        "output_token_cap": 100,
    }
    values.update(overrides)
    return LedgerRow(**values)  # type: ignore[arg-type]


NO_USAGE: dict[str, object] = {
    "input_tokens": None,
    "output_tokens": None,
    "cache_creation_tokens": None,
    "cache_read_tokens": None,
    "estimated_cost": None,
}
# claude-dear-output, bound 1000 and cap 100: 1000 x max(3, 2.50) + 100 x 30 per million.
WORST_DEAR = Charge(tokens=1_100, cost=Decimal("0.006"))


# --- effective caps (Keputusan Lead #2) ---------------------------------------------------------------------


def test_effective_budget_per_field_precedence() -> None:
    task = BudgetFields(max_tokens=1_000)
    project = BudgetFields(max_cost_usd=Decimal("0.5"), max_tokens=5_000)
    assert effective_budget(task, project, DEFAULTS) == EffectiveBudget(
        max_cost_usd=Decimal("0.5"), max_tokens=1_000, max_duration_minutes=60, max_tool_iterations=None
    )


def test_null_inherits_the_next_level() -> None:
    task = parse_budget({"max_tokens": None, "max_duration_minutes": None, "max_tool_iterations": 7})
    project = parse_budget({"max_cost_usd": None, "max_tokens": None, "max_duration_minutes": 30})
    budget = effective_budget(task, project, DEFAULTS)
    assert (budget.max_tokens, budget.max_duration_minutes) == (400_000, 30)
    assert (budget.max_cost_usd, budget.max_tool_iterations) == (Decimal("2"), 7)
    project_only = effective_budget(BudgetFields(), BudgetFields(max_tool_iterations=3), DEFAULTS)
    assert project_only.max_tool_iterations == 3


def test_a_zero_cap_is_a_cap_not_a_missing_value() -> None:
    task = BudgetFields(max_cost_usd=Decimal(0), max_tokens=0, max_tool_iterations=0, max_duration_minutes=0)
    project = BudgetFields(
        max_cost_usd=Decimal(1), max_tokens=10, max_tool_iterations=5, max_duration_minutes=9
    )
    assert effective_budget(task, project, DEFAULTS) == EffectiveBudget(Decimal(0), 0, 0, 0)
    assert effective_budget(BudgetFields(), task, DEFAULTS) == EffectiveBudget(Decimal(0), 0, 0, 0)


def test_no_tool_cap_without_a_value(table: PricingTable) -> None:
    budget = effective_budget(BudgetFields(), BudgetFields(), DEFAULTS)
    assert budget.max_tool_iterations is None
    usage = BudgetUsage(tokens=0, cost=Decimal(0), tool_iterations=10_000, elapsed=None)
    evaluate(budget, usage, CallBound("claude-cheap", 10, 10), table)


def test_env_cost_default_is_exact() -> None:
    defaults = BudgetDefaults.from_settings(load_settings(make_test_env(TASK_MAX_COST_USD="0.1")))
    assert defaults.max_cost_usd == Decimal("0.1")
    assert isinstance(defaults.max_cost_usd, Decimal)
    assert (defaults.max_tokens, defaults.max_duration_minutes) == (400_000, 60)


@pytest.mark.parametrize(
    "raw",
    [
        {"max_cost_usd": 2.5},  # a float: JSONB reads money back inexactly
        {"max_cost_usd": "-1"},
        {"max_cost_usd": "NaN"},
        {"max_cost_usd": "Infinity"},
        {"max_cost_usd": True},
        {"max_cost_usd": "abc"},
        {"max_token": 5},  # an unknown key
        {"max_tokens": 2.0},
        {"max_tokens": True},
        {"max_tokens": -1},
        {"max_tokens": "5"},
        {"max_duration_minutes": [60]},
        [],
        "abc",
        5,
    ],
)
def test_malformed_budget_fails_closed(raw: object) -> None:
    with pytest.raises(BudgetConfigError):
        parse_budget(raw)


def test_dump_then_parse_round_trips() -> None:
    fields = BudgetFields(max_cost_usd=Decimal("2.50"), max_tokens=1_000, max_tool_iterations=0)
    dumped = dump_budget(fields)
    assert dumped == {
        "max_cost_usd": "2.5",
        "max_tokens": 1_000,
        "max_tool_iterations": 0,
        "max_duration_minutes": None,
    }
    assert tuple(dumped) == BUDGET_KEYS
    assert parse_budget(json.loads(json.dumps(dumped))) == fields  # as JSONB gives it back
    assert parse_budget(None) == parse_budget({}) == BudgetFields()
    assert parse_budget({"max_cost_usd": 3}) == BudgetFields(max_cost_usd=Decimal(3))


# --- charge per ledger row (Keputusan Lead #6, Keputusan plan #4) ------------------------------------------


def test_ok_row_counts_recorded_tokens_and_cost(table: PricingTable) -> None:
    assert charge(row(), table) == Charge(tokens=130, cost=Decimal("0.0012"))


def test_ok_row_of_unknown_model_costs_the_ceiling(table: PricingTable) -> None:
    million = {f"{k}_tokens": 1_000_000 for k in ("input", "output", "cache_creation", "cache_read")}
    unpriced = row(model="claude-unpriced", estimated_cost=None, **million)
    # Ceiling of the whole table: input 4, output 30, cache write 5, cache read 0.40.
    assert charge(unpriced, table) == Charge(tokens=3_000_000, cost=Decimal("39.40"))


def test_ok_row_without_usage_counts_the_bound(table: PricingTable) -> None:
    assert charge(row(**NO_USAGE), table) == WORST_DEAR


REJECTED = frozenset({"rate_limited", "overloaded", "bad_request", "auth"})


@pytest.mark.parametrize("kind", sorted(REJECTED))
def test_rejected_before_processing_counts_zero(table: PricingTable, kind: str) -> None:
    assert charge(row(status="error", error_kind=kind, **NO_USAGE), table) == Charge(0, Decimal(0))


@pytest.mark.parametrize("kind", [*sorted(ERROR_KINDS - REJECTED), None])
def test_only_the_four_rejected_kinds_count_zero(table: PricingTable, kind: str | None) -> None:
    assert REJECTED == REJECTED_BEFORE_PROCESSING
    status = "cancelled" if kind == "cancelled" else "error"
    assert charge(row(status=status, error_kind=kind, **NO_USAGE), table) == WORST_DEAR


def test_rejected_kind_with_usage_counts_partially(table: PricingTable) -> None:
    rejected = row(
        status="error",
        error_kind="rate_limited",
        input_tokens=50,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        estimated_cost=Decimal("0.00015"),
    )
    # 50 x 3 + max(0, 100) x 30 per million: a stream that started is never free.
    assert charge(rejected, table) == Charge(tokens=150, cost=Decimal("0.00315"))


def test_failed_without_usage_counts_bound_and_cap(table: PricingTable) -> None:
    assert charge(row(status="error", error_kind="transient", **NO_USAGE), table) == WORST_DEAR
    assert charge(row(status="error", error_kind="timeout", **NO_USAGE), table) == WORST_DEAR


def test_cancelled_without_usage_counts_bound_and_cap(table: PricingTable) -> None:
    assert charge(row(status="cancelled", error_kind="cancelled", **NO_USAGE), table) == WORST_DEAR


def test_partial_usage_counts_the_output_cap(table: PricingTable) -> None:
    stalled = row(
        status="cancelled",
        error_kind="cancelled",
        input_tokens=50,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        output_token_cap=8_192,
        estimated_cost=Decimal("0.00015"),
    )
    assert charge(stalled, table).tokens == 8_242


def test_partial_row_costs_the_worst_case(table: PricingTable) -> None:
    partial = row(
        status="error",
        error_kind="transient",
        model="claude-tiered",
        input_tokens=1_000,
        output_tokens=10,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        estimated_cost=Decimal("0.0001"),  # the recorded lower bound is not what the budget counts
    )
    # Tiered ceiling (long-prompt tier): 1000 x 4 + max(10, 100) x 20 per million.
    assert charge(partial, table) == Charge(tokens=1_100, cost=Decimal("0.006"))
    past_cap = dataclasses.replace(partial, output_tokens=500)  # more output than the cap: what was seen
    assert charge(past_cap, table) == Charge(tokens=1_500, cost=Decimal("0.014"))


def test_cache_read_is_not_in_the_token_count(table: PricingTable) -> None:
    assert charge(row(cache_read_tokens=1_000_000), table).tokens == 130
    partial = row(status="error", error_kind="transient", cache_read_tokens=1_000_000)
    assert charge(partial, table).tokens == 100 + 10 + 100


def test_cache_read_is_in_the_cost(table: PricingTable) -> None:
    partial = row(
        status="error",
        error_kind="transient",
        model="claude-tiered",
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=1_000_000,
        output_token_cap=1,
    )
    assert charge(partial, table).cost == Decimal("0.40") + Decimal(20) / MILLION
    unpriced = row(
        model="claude-unpriced",
        estimated_cost=None,
        input_tokens=0,
        output_tokens=0,
        cache_creation_tokens=0,
        cache_read_tokens=1_000_000,
    )
    assert charge(unpriced, table).cost == Decimal("0.40")


# --- worst-case prices (Keputusan plan #3) -----------------------------------------------------------------


def test_unknown_model_uses_the_highest_price_in_the_table(table: PricingTable) -> None:
    assert price_ceiling(table, "claude-unpriced") == PriceCeiling(
        input=Decimal(4), output=Decimal(30), cache_write=Decimal(5), cache_read=Decimal("0.40")
    )
    assert price_ceiling(table, "claude-cheap") == PriceCeiling(
        input=Decimal(1), output=Decimal(5), cache_write=Decimal("1.25"), cache_read=Decimal("0.10")
    )


def test_worst_case_takes_the_long_prompt_tier(table: PricingTable) -> None:
    assert price_ceiling(table, "claude-tiered") == PriceCeiling(
        input=Decimal(4), output=Decimal(20), cache_write=Decimal(5), cache_read=Decimal("0.40")
    )


def test_input_bound_is_priced_as_a_cache_write_when_dearer(table: PricingTable) -> None:
    # claude-tiered: cache write 5 > input 4, so 1000 x 5 + 100 x 20.
    assert bound_charge(CallBound("claude-tiered", 1_000, 100), table) == Charge(1_100, Decimal("0.007"))
    # claude-dear-output: input 3 > cache write 2.50, so 1000 x 3 + 100 x 30.
    assert bound_charge(CallBound("claude-dear-output", 1_000, 100), table) == WORST_DEAR
    # unknown: max(4, 5) x 1000 + 30 x 100.
    assert bound_charge(CallBound("claude-unpriced", 1_000, 100), table) == Charge(1_100, Decimal("0.008"))


def test_cost_cannot_be_bounded_without_prices(empty_table: PricingTable) -> None:
    assert price_ceiling(empty_table, "claude-cheap") is None
    assert bound_charge(CallBound("claude-cheap", 10, 10), empty_table) == Charge(20, None)
    budget = effective_budget(BudgetFields(), BudgetFields(), DEFAULTS)
    fresh = BudgetUsage(tokens=0, cost=Decimal(0), tool_iterations=0, elapsed=None)
    with pytest.raises(BudgetExceeded) as raised:
        evaluate(budget, fresh, CallBound("claude-cheap", 10, 10), empty_table)
    assert (raised.value.kind, raised.value.used, raised.value.next_bound) == ("cost", Decimal(0), None)
    # A failed row cannot be priced either: the ledger's own cost is unknown.
    assert charge(row(status="error", error_kind="timeout", **NO_USAGE), empty_table) == Charge(1_100, None)
    unknown = BudgetUsage(tokens=1_100, cost=None, tool_iterations=0, elapsed=None)
    with pytest.raises(BudgetExceeded) as raised:
        evaluate(budget, unknown, CallBound("claude-cheap", 10, 10), empty_table)
    assert (raised.value.kind, raised.value.used, raised.value.next_bound) == ("cost", None, None)


# --- checks (Keputusan plan #5) -----------------------------------------------------------------------------

BUDGET = EffectiveBudget(
    max_cost_usd=Decimal("0.01"), max_tokens=10_000, max_duration_minutes=60, max_tool_iterations=None
)
NEXT = CallBound("claude-dear-output", 1_000, 100)  # bound: 1100 tokens, 0.006 USD


def usage(**overrides: object) -> BudgetUsage:
    values: dict[str, object] = {"tokens": 0, "cost": Decimal(0), "tool_iterations": 0, "elapsed": None}
    values.update(overrides)
    return BudgetUsage(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("used", "kind", "limit", "spent", "bound"),
    [
        ({"tokens": 9_000}, "tokens", 10_000, 9_000, 1_100),
        ({"cost": Decimal("0.005")}, "cost", Decimal("0.01"), Decimal("0.005"), Decimal("0.006")),
    ],
)
def test_next_attempt_bound_counts_against_the_cap(
    table: PricingTable, used: dict[str, object], kind: str, limit: object, spent: object, bound: object
) -> None:
    with pytest.raises(BudgetExceeded) as raised:
        evaluate(BUDGET, usage(**used), NEXT, table)
    assert (raised.value.kind, raised.value.limit, raised.value.used, raised.value.next_bound) == (
        kind,
        limit,
        spent,
        bound,
    )


def test_cap_reached_exactly_is_allowed(table: PricingTable) -> None:
    evaluate(BUDGET, usage(tokens=8_900), NEXT, table)
    evaluate(BUDGET, usage(cost=Decimal("0.004")), NEXT, table)
    evaluate(BUDGET, usage(tokens=8_900, cost=Decimal("0.004")), NEXT, table)


def test_tool_iterations_at_the_cap_pass(table: PricingTable) -> None:
    capped = EffectiveBudget(Decimal(1), 10_000, 60, max_tool_iterations=5)
    evaluate(capped, usage(tool_iterations=5), NEXT, table)


def test_tool_iterations_over_the_cap_refuse(table: PricingTable) -> None:
    capped = EffectiveBudget(Decimal(1), 10_000, 60, max_tool_iterations=5)
    with pytest.raises(BudgetExceeded) as raised:
        evaluate(capped, usage(tool_iterations=6), NEXT, table)
    assert (raised.value.kind, raised.value.limit, raised.value.used, raised.value.next_bound) == (
        "tool_iterations",
        5,
        6,
        0,
    )
    zero = EffectiveBudget(Decimal(1), 10_000, 60, max_tool_iterations=0)
    evaluate(zero, usage(tool_iterations=0), NEXT, table)
    with pytest.raises(BudgetExceeded):
        evaluate(zero, usage(tool_iterations=1), NEXT, table)


def test_duration_at_the_limit_refuses(table: PricingTable) -> None:
    with pytest.raises(BudgetExceeded) as raised:
        evaluate(BUDGET, usage(elapsed=timedelta(minutes=60)), NEXT, table)
    assert (raised.value.kind, raised.value.limit, raised.value.used, raised.value.next_bound) == (
        "duration",
        60,
        Decimal(60),
        0,
    )


def test_duration_just_under_the_limit_passes(table: PricingTable) -> None:
    evaluate(BUDGET, usage(elapsed=timedelta(minutes=60) - timedelta(microseconds=1)), NEXT, table)


def test_duration_without_started_at_is_not_checked(table: PricingTable) -> None:
    short = EffectiveBudget(Decimal(1), 10_000, max_duration_minutes=1, max_tool_iterations=None)
    evaluate(short, usage(elapsed=None), NEXT, table)


def test_checks_run_in_order(table: PricingTable) -> None:
    capped = EffectiveBudget(Decimal("0.01"), 10_000, 60, max_tool_iterations=0)
    over_all = usage(elapsed=timedelta(hours=2), tool_iterations=1, tokens=10_000, cost=Decimal(1))
    cases = [
        (over_all, "duration"),
        (usage(tool_iterations=1, tokens=10_000, cost=Decimal(1)), "tool_iterations"),
        (usage(tokens=10_000, cost=Decimal(1)), "tokens"),
        (usage(cost=Decimal(1)), "cost"),
    ]
    for used, kind in cases:
        with pytest.raises(BudgetExceeded) as raised:
            evaluate(capped, used, NEXT, table)
        assert raised.value.kind == kind


def test_budget_exceeded_carries_kind_limit_used_and_bound() -> None:
    error = BudgetExceeded("tokens", 10_000, 9_000, next_bound=1_100)
    assert (error.kind, error.limit, error.used, error.next_bound) == ("tokens", 10_000, 9_000, 1_100)
    assert "tokens" in str(error)
    assert BudgetExceeded("duration", 60, Decimal(61)).next_bound == 0


def test_uncounted_tool_statuses_are_real_statuses() -> None:
    assert set(UNCOUNTED_TOOL_STATUSES) == {"denied", "approval_required"}
    assert set(UNCOUNTED_TOOL_STATUSES) <= set(TOOL_CALL_STATUSES)


def test_call_bound_refuses_bad_bounds() -> None:
    with pytest.raises(ValueError):
        CallBound("claude-cheap", -1, 10)
    with pytest.raises(ValueError):
        CallBound("claude-cheap", 10, 0)
