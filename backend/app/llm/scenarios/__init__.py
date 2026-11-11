"""Scripted scenarios by name (ADR-015). Per-agent scenarios (``calc``, …) come with the runtime (3.1f)."""

from __future__ import annotations

from collections.abc import Mapping

from app.llm.scenarios import faults, generic, readiness
from app.llm.scenarios.model import Scenario

SCENARIOS: Mapping[str, Scenario] = {
    scenario.name: scenario for scenario in (generic.SCENARIO, faults.SCENARIO, readiness.SCENARIO)
}
