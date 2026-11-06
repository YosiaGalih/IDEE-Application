"""Agent status (FR-045, spec WP-3.1b goal 4): computed when read, never stored.

``ready`` is a claim that the agent can run now. It is only made when nothing it needs is missing: its
runtime (the AgentRuntime for llm/profile agents, WP-3.1f; the workflow engine for tool-mode agents and
ORCH, WP-3.2, since the orchestrator runs their tools in their name, AGENT_CATALOG §4.3), a provider
that passed the readiness gate (WP-3.1e) for agents that call the model, and every allowlisted tool built.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from app.tools.catalog import TOOL_CATALOG

AgentStatus = Literal["planned", "disabled", "not_ready", "ready"]
StatusReason = Literal[
    "runtime_unavailable", "provider_readiness_unknown", "provider_not_ready", "tools_unavailable"
]


class AgentLike(Protocol):
    """A registry entry or a synced ``agent_profiles`` row."""

    @property
    def mvp_mode(self) -> str: ...
    @property
    def group(self) -> str: ...
    @property
    def tools(self) -> Sequence[str]: ...


# Flipped by the work package that builds each runtime.
RUNTIMES_AVAILABLE: Mapping[str, bool] = {"agent_runtime": False, "workflow_engine": False}


def assess(
    agent: AgentLike, *, enabled: bool, provider_ready: bool | None
) -> tuple[AgentStatus, list[StatusReason], list[str]]:
    """(status, reasons, unavailable tools). ``provider_ready`` is None while no readiness result exists."""
    if agent.mvp_mode == "planned":
        return "planned", [], []
    if not enabled:
        return "disabled", [], []
    reasons: list[StatusReason] = []
    calls_model = agent.mvp_mode in {"llm", "profile"}
    runtime = "agent_runtime" if calls_model else "workflow_engine"
    if not RUNTIMES_AVAILABLE[runtime]:
        reasons.append("runtime_unavailable")
    if calls_model and provider_ready is None:
        reasons.append("provider_readiness_unknown")
    elif calls_model and not provider_ready:
        reasons.append("provider_not_ready")
    # A tool the catalog no longer knows (a stale row) is as unavailable as an unbuilt one.
    unavailable = [t for t in agent.tools if t != "submit_result" and not _built(t)]
    if unavailable:
        reasons.append("tools_unavailable")
    return ("not_ready" if reasons else "ready"), reasons, unavailable


def _built(tool: str) -> bool:
    info = TOOL_CATALOG.get(tool)
    return info is not None and info.implemented
