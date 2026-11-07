"""``grant_for``: what the gateway lets an agent do comes from the validated registry in memory (spec WP-3.1d
Goal 1; 3.1b AG-13 C3). The import-linter contract keeps app.tools and app.agents away from agent_profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.grants import AgentNotRunnable, grant_for
from app.agents.registry import load_registry

REGISTRY = load_registry(Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml")


@pytest.mark.parametrize("agent_id", ["B14", "D22", "A07", "E27"])
def test_the_grant_is_the_registry_entry(agent_id: str) -> None:
    agent = REGISTRY.by_id(agent_id)
    assert agent is not None
    grant = grant_for(REGISTRY, agent_id)
    assert grant.agent_id == agent_id
    assert grant.permissions is agent.permissions  # the validated object itself, not a copy from a row
    assert grant.tools == frozenset(agent.tools) - {"submit_result"}


def test_submit_result_is_never_a_gateway_tool() -> None:
    assert all(
        "submit_result" not in grant_for(REGISTRY, a.agent_id).tools for a in REGISTRY.agents if a.enabled
    )


@pytest.mark.parametrize("agent_id", ["Z99", "I42"])
def test_unknown_or_planned_agents_get_no_grant(agent_id: str) -> None:
    with pytest.raises(AgentNotRunnable):
        grant_for(REGISTRY, agent_id)


def test_the_grant_cannot_be_widened() -> None:
    grant = grant_for(REGISTRY, "D22")
    with pytest.raises(AttributeError):
        grant.tools = frozenset({"code.write_file"})  # type: ignore[misc]
    with pytest.raises(TypeError):
        grant.tool_quota["repo.read_file"] = 1  # type: ignore[index]
