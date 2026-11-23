"""Assembles the task engine from its ports (spec WP-3.2 §14.4a). The worker calls this per job."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.registry import AgentRegistry
from app.agents.runtime import ToolExecutor
from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.events import EventBus
from app.llm.pricing import PricingTable
from app.services.agent_runtime import ProviderFactory, build_agent_runtime
from app.services.agent_step import AgentStep, DbAgentStepReads, bind_agent_steps, validate_agent_steps
from app.workflow.engine import Step, TaskEngine, WorkflowPorts

# The agent steps of the production engine. Empty until a later work package registers its steps and flips
# ``RUNTIMES_AVAILABLE["workflow_engine"]`` (N27).
AGENT_STEPS: Mapping[str, AgentStep] = MappingProxyType({})


def build_orchestrator(
    *,
    sessions: async_sessionmaker[AsyncSession],
    bus: EventBus,
    audit: AuditWriter,
    settings: Settings,
    worker_id: str,
    registry: AgentRegistry,
    pricing: PricingTable,
    steps: Mapping[str, Step] | None = None,
    agent_steps: Mapping[str, AgentStep] | None = None,
    providers: ProviderFactory | None = None,
    tools: ToolExecutor | None = None,
) -> TaskEngine:
    """``steps``, ``agent_steps``, ``providers`` and ``tools`` are test seams: production passes ``None``
    (the registry of agent steps is ``AGENT_STEPS``). The agent runtime is built only when there is an agent
    step, with the same ``worker_id`` the engine claims with, so its heartbeat renews that owner's lease.

    ``registry`` and ``pricing`` are required on every delivery, even while ``AGENT_STEPS`` is empty: a worker
    that has not loaded them fails at once, before any connection or claim, rather than on the first agent
    step. The registration is validated (pure, no SQL) before the runtime is built, because that build reads
    the prompt book from disk."""
    agent_specs = AGENT_STEPS if agent_steps is None else agent_steps
    plain: dict[str, Step] = dict(steps) if steps is not None else {}
    clash = sorted(set(plain) & set(agent_specs))
    if clash:
        raise ValueError("step registered twice: " + ", ".join(clash))
    validate_agent_steps(agent_specs, registry)
    if agent_specs:
        runtime = build_agent_runtime(
            sessions=sessions,
            settings=settings,
            registry=registry,
            pricing=pricing,
            audit=audit,
            bus=bus,
            worker_id=worker_id,
            tools=tools,
            providers=providers,
        )
        plain.update(
            bind_agent_steps(
                agent_specs, runtime=runtime, reads=DbAgentStepReads(sessions), registry=registry
            )
        )
    return TaskEngine(
        WorkflowPorts(
            sessions=sessions,
            bus=bus,
            audit=audit,
            worker_id=worker_id,
            max_active=settings.max_active_tasks,
            steps=plain,
            delivery_limit_s=settings.task_delivery_max_minutes * 60,
        )
    )
