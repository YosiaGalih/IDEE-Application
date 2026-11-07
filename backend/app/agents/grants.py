"""What the ToolGateway lets an agent do, from the validated registry (spec WP-3.1d Goal 1; 3.1b AG-13 C3).

The registry loaded and validated at start (``app.agents.registry``) is the only source: ``agent_profiles``
rows are a synced copy the runtime role may UPDATE between two syncs, so permissions are never read from
them (an import-linter contract keeps ``app.agents`` and ``app.tools`` away from ``app.models.agents``).
"""

from __future__ import annotations

from types import MappingProxyType

from app.agents.registry import AgentRegistry
from app.tools.base import AgentGrant


class AgentNotRunnable(Exception):
    """The agent is not in the registry, or not enabled: it gets no grant at all."""


def grant_for(registry: AgentRegistry, agent_id: str) -> AgentGrant:
    agent = registry.by_id(agent_id)
    if agent is None or not agent.enabled:
        raise AgentNotRunnable(f"{agent_id} is not an enabled agent of the registry")
    return AgentGrant(
        agent_id=agent.agent_id,
        # submit_result is the runtime's terminal tool (ADR-013), never a gateway call.
        tools=frozenset(t for t in agent.tools if t != "submit_result"),
        permissions=agent.permissions,
        tool_quota=MappingProxyType(dict(agent.limits.tool_quota or {})),
    )
