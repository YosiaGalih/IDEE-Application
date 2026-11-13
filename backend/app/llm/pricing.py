"""Versioned model prices (FR-134, ADR-004, ADR-025).

Costs are estimates from a dated table, never provider-reported figures (``actual`` stays reserved). An
unknown model or a missing usage gives ``None`` with ``cost_status = "unavailable"``: never a zero that looks
like a price.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from app.llm.base import Usage

# What an estimate can say about its cost; "actual" (provider-reported, ADR-025) is never estimated.
EstimateStatus = Literal["estimated", "partial", "unavailable"]

_MILLION = Decimal(1_000_000)


class PricingError(Exception):
    """The pricing file is missing or invalid. Raised instead of silently pricing calls at zero."""


def _exact_number(value: object) -> object:
    # Floats lose precision (3.1 is not 3.1), so prices are written as strings or integers.
    if isinstance(value, float):
        raise ValueError('write prices as strings, e.g. "3.75"')
    return value


# Zero is refused too: a "0" typo would price every call at nothing while looking like a real price.
Price = Annotated[Decimal, BeforeValidator(_exact_number), Field(gt=0, allow_inf_nan=False)]


class _Prices(BaseModel):
    """USD per million tokens; ``cache_write`` is the 5-minute cache write price (the adapter's TTL)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: Price
    output: Price
    cache_write: Price
    cache_read: Price

    def cost(self, usage: Usage) -> Decimal:
        return (
            usage.input_tokens * self.input
            + usage.output_tokens * self.output
            + usage.cache_creation_tokens * self.cache_write
            + usage.cache_read_tokens * self.cache_read
        ) / _MILLION


class LongPromptPrice(_Prices):
    """Prices for a request whose prompt (all input tokens, cache reads and writes included) exceeds
    ``above_tokens``; the provider then prices the whole request at this tier."""

    above_tokens: int = Field(gt=0)


class ModelPrice(_Prices):
    long_prompt: LongPromptPrice | None = None

    def for_usage(self, usage: Usage) -> _Prices:
        prompt = usage.input_tokens + usage.cache_creation_tokens + usage.cache_read_tokens
        if self.long_prompt is not None and prompt > self.long_prompt.above_tokens:
            return self.long_prompt
        return self


@dataclass(frozen=True, slots=True)
class CostEstimate:
    estimated_cost: Decimal | None
    cost_status: EstimateStatus
    pricing_version: str


class PricingTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=40)
    currency: Literal["USD"]
    source: str = Field(min_length=1)
    verify_before_production: bool
    # At least one model: an empty table would price every call "unavailable" and bound no budget (review L1).
    models: Mapping[str, ModelPrice] = Field(min_length=1)

    def estimate(self, model: str, usage: Usage | None, *, partial: bool) -> CostEstimate:
        """``partial`` must be stated: True for an attempt that failed or was cancelled after it started.
        Its usage is a lower bound only: the provider reports the output count at the end of the stream, so
        a cut attempt may show 1 output token for thousands billed (AG-13 M2; the budget allows for it, 3.1f).
        """
        price = self.models.get(model)
        if price is None or usage is None:
            return CostEstimate(None, "unavailable", self.version)
        # Exact (prices are decimals, counts integers): rounding a tiny call to 0.000000 would read as a price
        # of zero. The ledger chooses its stored precision (WP-3.1f).
        cost = price.for_usage(usage).cost(usage)
        return CostEstimate(cost, "partial" if partial else "estimated", self.version)


def load_pricing(path: Path) -> PricingTable:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PricingError(f"cannot read pricing file {path}: {type(exc).__name__}") from exc
    try:
        return PricingTable.model_validate(raw)
    except ValidationError as exc:
        raise PricingError(f"invalid pricing file {path}: {exc.error_count()} error(s)\n{exc}") from exc
