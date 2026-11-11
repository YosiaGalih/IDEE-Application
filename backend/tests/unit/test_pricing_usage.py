from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import get_args, get_type_hints

import pytest

from app.llm.base import ERROR_KINDS, Usage
from app.llm.pricing import PricingError, PricingTable, load_pricing
from app.llm.retry import AttemptRecord
from app.models.enums import LLM_ERROR_KINDS, USAGE_ATTEMPT_STATUSES

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


def test_ledger_enums_match_the_llm_port() -> None:
    """``app.models`` cannot import ``app.llm``: the ledger CHECKs copy the port's values, kept equal here."""
    assert set(LLM_ERROR_KINDS) == ERROR_KINDS
    assert len(LLM_ERROR_KINDS) == len(ERROR_KINDS)
    assert USAGE_ATTEMPT_STATUSES == get_args(get_type_hints(AttemptRecord)["status"])
