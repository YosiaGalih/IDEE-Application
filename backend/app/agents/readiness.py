"""Agent status (FR-045, spec WP-3.1b goal 4): computed when read, never stored.

``ready`` is a claim that the agent can run now. It is only made when nothing it needs is missing: its
runtime (the AgentRuntime for llm/profile agents, WP-3.1f; the workflow engine for ORCH, WP-3.2), a provider
that passed the readiness gate (WP-3.1e) for agents that call the model, and every allowlisted tool built.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from app.agents.registry import AgentSpec
from app.tools.catalog import TOOL_CATALOG

AgentStatus = Literal["planned", "disabled", "not_ready", "ready"]
StatusReason = Literal[
    "runtime_unavailable", "provider_readiness_unknown", "provider_not_ready", "tools_unavailable"
]

# Flipped by the work package that builds each runtime.
RUNTIMES_AVAILABLE: Mapping[str, bool] = {"agent_runtime": False, "workflow_engine": False}


def assess(
    agent: AgentSpec, *, enabled: bool, provider_ready: bool | None
) -> tuple[AgentStatus, list[StatusReason], list[str]]:
    """(status, reasons, unavailable tools). ``provider_ready`` is None while no readiness result exists."""
    if agent.mvp_mode == "planned":
        return "planned", [], []
    if not enabled:
        return "disabled", [], []
    reasons: list[StatusReason] = []
    calls_model = agent.mvp_mode in {"llm", "profile"}
    runtime = (
        "agent_runtime" if calls_model else "workflow_engine" if agent.group == "orchestration" else None
    )
    if runtime is not None and not RUNTIMES_AVAILABLE[runtime]:
        reasons.append("runtime_unavailable")
    if calls_model and provider_ready is None:
        reasons.append("provider_readiness_unknown")
    elif calls_model and not provider_ready:
        reasons.append("provider_not_ready")
    unavailable = [t for t in agent.tools if t != "submit_result" and not TOOL_CATALOG[t].implemented]
    if unavailable:
        reasons.append("tools_unavailable")
    return ("not_ready" if reasons else "ready"), reasons, unavailable
