"""Agent status (FR-045, spec WP-3.1b goal 4): "ready" only when nothing the agent needs is missing."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.agents import readiness
from app.agents.registry import load_registry, parse_registry
from app.tools import catalog

REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"
Raw = dict[str, Any]


@pytest.fixture(scope="module")
def raw() -> Raw:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def agent(data: Raw, agent_id: str) -> Raw:
    return next(entry for entry in data["agents"] if entry["agent_id"] == agent_id)


def registry_agent(agent_id: str):  # type: ignore[no-untyped-def]
    found = load_registry(REGISTRY).by_id(agent_id)
    assert found is not None
    return found


def test_enabled_llm_agent_is_not_ready_with_all_reasons() -> None:
    a02 = registry_agent("A02")
    status, reasons, unavailable = readiness.assess(a02, enabled=True, provider_ready=None)
    assert status == "not_ready"
    assert reasons == ["runtime_unavailable", "provider_readiness_unknown", "tools_unavailable"]
    assert unavailable == [tool for tool in a02.tools if tool != "submit_result"]


def test_tool_agent_waits_for_the_engine_and_its_tools() -> None:
    # Tool-mode agents are invoked by the orchestrator (AGENT_CATALOG §4.3), not by themselves.
    a04 = registry_agent("A04")
    status, reasons, _ = readiness.assess(a04, enabled=True, provider_ready=None)
    assert (status, reasons) == ("not_ready", ["runtime_unavailable", "tools_unavailable"])


def test_no_agent_is_ready_while_no_runtime_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every tool built and the provider ready: the runtimes alone still decide (AG-13 L1, silent M2).
    monkeypatch.setattr(readiness, "_built", lambda tool: True)
    statuses = {
        a.agent_id: readiness.assess(a, enabled=a.enabled, provider_ready=True)[0]
        for a in load_registry(REGISTRY).agents
    }
    assert "ready" not in statuses.values()


def test_a_tool_agent_without_tools_still_waits_for_the_engine(raw: Raw) -> None:
    # Proves the runtime requirement only: with the engine built, an agent with no tool would read ready.
    data = copy.deepcopy(raw)
    agent(data, "A04")["tools"] = []
    a04 = parse_registry(data).by_id("A04")
    assert a04 is not None
    assert readiness.assess(a04, enabled=True, provider_ready=True)[0] == "not_ready"


def test_orch_waits_for_the_engine() -> None:
    orch = registry_agent("ORCH")
    assert readiness.assess(orch, enabled=True, provider_ready=True) == (
        "not_ready",
        ["runtime_unavailable"],
        [],
    )


def test_planned_and_disabled() -> None:
    planned = next(a for a in load_registry(REGISTRY).agents if a.mvp_mode == "planned")
    assert readiness.assess(planned, enabled=False, provider_ready=True) == ("planned", [], [])
    assert readiness.assess(registry_agent("A02"), enabled=False, provider_ready=True) == ("disabled", [], [])


def test_provider_not_ready_is_its_own_reason() -> None:
    _, reasons, _ = readiness.assess(registry_agent("A02"), enabled=True, provider_ready=False)
    assert "provider_not_ready" in reasons
    assert "provider_readiness_unknown" not in reasons


def test_ready_only_without_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    built = {
        name: catalog.ToolInfo(t.name, t.risk, t.executor, t.preconditions, t.status, True)
        for name, t in catalog.TOOL_CATALOG.items()
    }
    monkeypatch.setattr(readiness, "TOOL_CATALOG", built)
    monkeypatch.setattr(readiness, "RUNTIMES_AVAILABLE", {"agent_runtime": True, "workflow_engine": True})
    assert readiness.assess(registry_agent("A02"), enabled=True, provider_ready=True) == ("ready", [], [])
