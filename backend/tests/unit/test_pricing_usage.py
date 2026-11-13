from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Literal, get_args, get_type_hints

import pytest

from app.llm.base import ERROR_KINDS, ErrorKind, Usage
from app.llm.pricing import PricingError, PricingTable, load_pricing
from app.llm.retry import AttemptRecord
from app.models.enums import LLM_ERROR_KINDS, USAGE_ATTEMPT_STATUSES
from app.services.usage import EMPTY_TOTALS, UsageCallContext, UsageTotals, aggregate, build_row

BACKEND = Path(__file__).resolve().parents[2]

TABLE = """
version: "2026-11-05"
currency: USD
source: https://example.test/pricing
verify_before_production: true
models:
  claude-test-model:
    input: "3"
    output: "15"
    cache_write: "3.75"
    cache_read: "0.30"
  claude-tiered-model:
    input: "0.10"
    output: "0.50"
    cache_write: "0.125"
    cache_read: "0.01"
    long_prompt:
      above_tokens: 100000
      input: "0.50"
      output: "2.50"
      cache_write: "0.625"
      cache_read: "0.05"
"""


@pytest.fixture
def table(tmp_path: Path) -> PricingTable:
    path = tmp_path / "pricing.yaml"
    path.write_text(TABLE, encoding="utf-8")
    return load_pricing(path)


def test_estimate_known_model(table: PricingTable) -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    estimate = table.estimate("claude-test-model", usage, partial=False)
    assert estimate.estimated_cost == Decimal("22.05")
    assert estimate.cost_status == "estimated"
    assert estimate.pricing_version == "2026-11-05"


def test_estimate_is_exact(table: PricingTable) -> None:
    estimate = table.estimate("claude-test-model", Usage(input_tokens=1, output_tokens=0), partial=False)
    assert estimate.estimated_cost == Decimal("0.000003")
    # A tiny call is never rounded to a zero that looks like a price; the ledger decides its own precision.
    tiny = table.estimate(
        "claude-test-model", Usage(input_tokens=0, output_tokens=0, cache_read_tokens=1), partial=False
    )
    assert tiny.estimated_cost == Decimal("0.0000003")


def test_partial_must_be_stated(table: PricingTable) -> None:
    with pytest.raises(TypeError):
        table.estimate("claude-test-model", Usage(input_tokens=1, output_tokens=0))  # type: ignore[call-arg]


def test_prompt_at_threshold_uses_base_prices(table: PricingTable) -> None:
    usage = Usage(input_tokens=60_000, output_tokens=1_000_000, cache_read_tokens=40_000)  # prompt = 100_000
    estimate = table.estimate("claude-tiered-model", usage, partial=False)
    assert estimate.estimated_cost == Decimal("0.5064")  # 0.006 + 0.5 + 0.0004


def test_long_prompt_counts_cache_tokens_and_uses_tier_prices(table: PricingTable) -> None:
    # The provider counts every input token, cache reads and writes included, towards the prompt length.
    usage = Usage(
        input_tokens=60_000, output_tokens=1_000_000, cache_creation_tokens=1, cache_read_tokens=40_000
    )
    estimate = table.estimate("claude-tiered-model", usage, partial=False)
    assert estimate.estimated_cost == Decimal("2.532000625")  # 0.03 + 2.5 + 0.000000625 + 0.002


def test_estimated_vs_unavailable(table: PricingTable) -> None:
    """TEST_STRATEGY AC-16: a known price gives an estimate; an unknown one says "unavailable", never 0."""
    usage = Usage(input_tokens=1_000, output_tokens=100)
    known = table.estimate("claude-test-model", usage, partial=False)
    unknown = table.estimate("claude-not-priced", usage, partial=False)
    assert (known.cost_status, known.estimated_cost) == ("estimated", Decimal("0.0045"))
    assert (unknown.cost_status, unknown.estimated_cost) == ("unavailable", None)


def test_unknown_model_is_unavailable_not_zero(table: PricingTable) -> None:
    estimate = table.estimate("claude-unknown", Usage(input_tokens=10, output_tokens=10), partial=False)
    assert estimate.estimated_cost is None
    assert estimate.cost_status == "unavailable"
    assert estimate.pricing_version == "2026-11-05"


@pytest.mark.parametrize("partial", [False, True])
def test_no_usage_is_unavailable(table: PricingTable, partial: bool) -> None:
    estimate = table.estimate("claude-test-model", None, partial=partial)
    assert estimate.estimated_cost is None
    assert estimate.cost_status == "unavailable"


def test_partial_usage_is_marked_partial(table: PricingTable) -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=0)
    estimate = table.estimate("claude-test-model", usage, partial=True)
    assert estimate.estimated_cost == Decimal("3")
    assert estimate.cost_status == "partial"


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (None, "missing file"),
        ("version: [unclosed", "broken YAML"),
        ("- a list", "not a mapping"),
        (TABLE.replace('input: "3"', 'input: "-3"'), "negative price"),
        (TABLE.replace('input: "3"', 'input: "three"'), "not a number"),
        (TABLE.replace('input: "3"', "input: 3.1"), "float price"),
        (TABLE.replace('cache_read: "0.30"', 'cache_read: "0.30"\n    batch: "1"'), "unknown model key"),
        (TABLE + "extra: true\n", "unknown top-level key"),
        (TABLE.replace('version: "2026-11-05"', 'version: ""'), "empty version"),
        (TABLE.replace("currency: USD", "currency: EUR"), "currency other than USD"),
        (TABLE.replace('    cache_read: "0.30"\n', ""), "missing price"),
        (TABLE.replace("above_tokens: 100000", "above_tokens: 0"), "tier threshold not positive"),
        (TABLE.replace('input: "3"', 'input: "0"'), "zero price (a typo that would look like a real price)"),
        (TABLE.split("models:")[0] + "models: {}\n", "no models (every call would be unpriced; review L1)"),
    ],
)
def test_load_rejects(tmp_path: Path, content: str | None, reason: str) -> None:
    path = tmp_path / "pricing.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(PricingError):
        load_pricing(path)


def test_repo_pricing_file_loads() -> None:
    table = load_pricing(BACKEND / "config" / "pricing.yaml")
    assert table.verify_before_production is True
    assert table.currency == "USD"
    assert table.version


def test_api_usage_literals_match_the_ledger_checks() -> None:
    """One CostStatus for the API (code review), equal to the values the ledger's CHECKs allow."""
    from app.api.routes.usage_out import AttemptStatus, CostStatus
    from app.models.enums import COST_STATUSES

    assert get_args(CostStatus) == COST_STATUSES
    assert get_args(AttemptStatus) == USAGE_ATTEMPT_STATUSES


def test_ledger_enums_match_the_llm_port() -> None:
    """``app.models`` cannot import ``app.llm``: the ledger CHECKs copy the port's values, kept equal here."""
    assert set(LLM_ERROR_KINDS) == ERROR_KINDS
    assert len(LLM_ERROR_KINDS) == len(ERROR_KINDS)
    assert USAGE_ATTEMPT_STATUSES == get_args(get_type_hints(AttemptRecord)["status"])


# --- ledger rows (WP-3.1f1 Task 2; spec WP-3.1f Keputusan Lead #3, #4) --------------------------------------

PROJECT = uuid.UUID(int=1)
CALL = uuid.UUID(int=2)


def ctx(**overrides: object) -> UsageCallContext:
    values: dict[str, object] = {
        "project_id": PROJECT,
        "call_id": CALL,
        "input_token_bound": 1_000,
        "output_token_cap": 8_192,
    }
    values.update(overrides)
    return UsageCallContext(**values)  # type: ignore[arg-type]


def attempt_record(
    *,
    status: Literal["ok", "error", "cancelled"] = "ok",
    error_kind: ErrorKind | None = None,
    usage: Usage | None = Usage(input_tokens=1_000, output_tokens=100),  # noqa: B008 - frozen
    model: str = "claude-test-model",
    attempt: int = 1,
    request_id: str | None = "req_1",
) -> AttemptRecord:
    return AttemptRecord("scripted", model, attempt, request_id, usage, 42, status, error_kind)


def test_row_from_ok_attempt_is_estimated(table: PricingTable) -> None:
    row = build_row(ctx(), attempt_record(), table)
    assert row["status"] == "ok" and row["error_kind"] is None
    assert row["cost_status"] == "estimated"
    assert row["estimated_cost"] == Decimal("0.0045")  # 1000 x 3 + 100 x 15 per million
    assert row["pricing_version"] == "2026-11-05"
    assert (row["input_tokens"], row["output_tokens"]) == (1_000, 100)
    assert (row["cache_creation_tokens"], row["cache_read_tokens"]) == (0, 0)
    assert (row["provider"], row["model"], row["duration_ms"]) == ("scripted", "claude-test-model", 42)
    assert (row["project_id"], row["call_id"]) == (PROJECT, CALL)


@pytest.mark.parametrize(("status", "kind"), [("error", "transient"), ("cancelled", "cancelled")])
def test_row_from_failed_attempt_with_usage_is_partial(
    table: PricingTable, status: Literal["error", "cancelled"], kind: ErrorKind
) -> None:
    usage = Usage(input_tokens=50, output_tokens=0)
    row = build_row(ctx(), attempt_record(status=status, error_kind=kind, usage=usage), table)
    assert (row["status"], row["error_kind"]) == (status, kind)
    assert row["cost_status"] == "partial"  # a lower bound, never a plain estimate (AG-13 3.1a M2)
    assert row["estimated_cost"] == Decimal("0.00015")
    assert (row["input_tokens"], row["output_tokens"]) == (50, 0)


@pytest.mark.parametrize(
    ("status", "kind"),
    [("error", "transient"), ("cancelled", "cancelled"), ("ok", None), ("error", None)],
)
def test_row_without_usage_is_unavailable(
    table: PricingTable, status: Literal["ok", "error", "cancelled"], kind: ErrorKind | None
) -> None:
    row = build_row(ctx(), attempt_record(status=status, error_kind=kind, usage=None), table)
    assert (row["cost_status"], row["estimated_cost"]) == ("unavailable", None)
    tokens = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")
    assert [row[name] for name in tokens] == [None, None, None, None]
    assert row["pricing_version"] == "2026-11-05"


def test_row_of_unknown_model_keeps_tokens_and_is_unavailable(table: PricingTable) -> None:
    usage = Usage(input_tokens=10, output_tokens=5, cache_creation_tokens=2, cache_read_tokens=3)
    row = build_row(ctx(), attempt_record(model="claude-unpriced", usage=usage), table)
    assert (row["cost_status"], row["estimated_cost"]) == ("unavailable", None)
    assert (row["input_tokens"], row["output_tokens"]) == (10, 5)
    assert (row["cache_creation_tokens"], row["cache_read_tokens"]) == (2, 3)


def test_row_cost_rounds_up_at_ten_places(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(TABLE.replace('input: "3"', 'input: "0.00001"'), encoding="utf-8")
    cheap = load_pricing(path)
    row = build_row(ctx(), attempt_record(usage=Usage(input_tokens=1, output_tokens=0)), cheap)
    # 1 token x 0.00001 / 1e6 = 1E-11: stored rounded up, never down to a zero that reads as a price.
    assert row["estimated_cost"] == Decimal("1E-10")
    assert row["cost_status"] == "estimated"


def test_row_keeps_attempt_retry_count_and_bounds(table: PricingTable) -> None:
    task_id, run_id = uuid.uuid4(), uuid.uuid4()
    context = ctx(
        task_id=task_id, agent_run_id=run_id, agent_id="B14", input_token_bound=77, output_token_cap=9
    )
    row = build_row(context, attempt_record(attempt=4, status="error", error_kind="overloaded"), table)
    assert (row["attempt"], row["retry_count"]) == (4, 3)
    assert (row["input_token_bound"], row["output_token_cap"]) == (77, 9)
    assert (row["task_id"], row["agent_run_id"], row["agent_id"]) == (task_id, run_id, "B14")


def test_row_truncates_request_id(table: PricingTable) -> None:
    row = build_row(ctx(), attempt_record(request_id="r" * 500), table)
    assert row["request_id"] == "r" * 120
    assert build_row(ctx(), attempt_record(request_id=None), table)["request_id"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_token_bound": -1},
        {"output_token_cap": 0},
        {"output_token_cap": -5},
        {"agent_run_id": uuid.UUID(int=3), "agent_id": "B14"},  # a run without its task
        {"agent_run_id": uuid.UUID(int=3), "task_id": uuid.UUID(int=4)},  # a run without its agent
    ],
)
def test_context_refuses_bad_bounds_and_run_without_task(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ctx(**overrides)


# --- totals (WP-3.1f1 Task 6; spec WP-3.1f Keputusan Lead #5) ----------------------------------------------


# (rows, partial, unavailable, rows with known tokens, known cost)
AGGREGATES = [
    ((0, 0, 0, 0, None), UsageTotals(0, 0, Decimal(0), "estimated")),  # no rows: a counted zero
    ((3, 0, 0, 3, Decimal("0.3")), UsageTotals(30, 3, Decimal("0.3"), "estimated")),
    ((3, 1, 0, 3, Decimal("0.3")), UsageTotals(30, 3, Decimal("0.3"), "partial")),
    (
        (3, 0, 1, 3, Decimal("0.2")),
        UsageTotals(30, 3, Decimal("0.2"), "partial"),
    ),  # the known sum: a lower bound
    ((3, 2, 1, 3, Decimal("0.2")), UsageTotals(30, 3, Decimal("0.2"), "partial")),
    ((3, 0, 3, 3, None), UsageTotals(30, 3, None, "unavailable")),  # never "0"
    # Silent-failure M1: rows exist but none reported tokens -> unknown, never a counted 0.
    ((2, 0, 2, 0, None), UsageTotals(None, None, None, "unavailable")),
    # Some rows reported tokens: their sum, a lower bound, with a status that is not "estimated".
    ((3, 0, 2, 1, Decimal("0.1")), UsageTotals(30, 3, Decimal("0.1"), "partial")),
]


@pytest.mark.parametrize(("counts", "expected"), AGGREGATES)
def test_aggregate_cost_status(
    counts: tuple[int, int, int, int, Decimal | None], expected: UsageTotals
) -> None:
    rows, partial, unavailable, counted, known_cost = counts
    totals = aggregate(
        rows=rows,
        partial=partial,
        unavailable=unavailable,
        counted=counted,
        known_cost=known_cost,
        input_tokens=10 * rows,
        output_tokens=rows,
    )
    assert totals == expected
    # What the dashboard schema requires: an amount exactly when the status is not "unavailable".
    assert (totals.estimated_cost is None) == (totals.cost_status == "unavailable")
    empty = aggregate(
        rows=0, partial=0, unavailable=0, counted=0, known_cost=None, input_tokens=0, output_tokens=0
    )
    assert empty == EMPTY_TOTALS
