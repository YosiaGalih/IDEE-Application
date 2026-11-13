"""Scripted scenarios by name (ADR-015). A run picks one with its ``scenario`` argument, for the scripted
provider only (WP-3.1f2a); without one it is ``generic``. The per-agent ``calc*`` scenarios come in
WP-3.1f3."""

from __future__ import annotations

from collections.abc import Mapping

from app.llm.scenarios import faults, generic, readiness
from app.llm.scenarios.model import Scenario

SCENARIOS: Mapping[str, Scenario] = {
    scenario.name: scenario for scenario in (generic.SCENARIO, faults.SCENARIO, readiness.SCENARIO)
}
