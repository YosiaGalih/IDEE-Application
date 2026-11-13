"""Types shared by the usage shapes of ``/usage`` and ``/dashboard`` (API_CONTRACTS §11.2, §11 #3)."""

from __future__ import annotations

from typing import Literal

# The API's cost_status, defined once. "actual" is reserved for provider-reported costs (ADR-025); the
# ledger itself only ever estimates (app.llm.pricing.EstimateStatus).
CostStatus = Literal["estimated", "partial", "actual", "unavailable"]
# What happened to one attempt (usage_records.status).
AttemptStatus = Literal["ok", "error", "cancelled"]
