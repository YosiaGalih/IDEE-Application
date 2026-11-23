"""Shared helpers of the engine end-to-end tests with the real agent runtime (WP-3.2c2c2 Tasks 5-7).

The engine is built by the real ``build_orchestrator`` (real ``AgentRuntime``, real database rows); a test
injects only through the seams ``providers=``, ``tools=``, ``registry=`` and ``agent_steps=``. Timing of the
engine is changed with ``retime`` (a copy of the ports), never by a parameter of ``app/``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.registry import AgentRegistry
from app.agents.runtime import ToolExecutor
from app.core.audit import AuditWriter
from app.core.cancel import CancelToken
from app.core.config import Settings
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.llm.base import LLMError, LLMRequest, LLMResponse, TextDeltaHandler
from app.llm.pricing import PricingTable
from app.services.agent_runtime import ProviderFactory
from app.services.agent_step import AgentStep, AgentSuccess
from app.services.agents import sync_registry
from app.services.orchestrator import build_orchestrator
from app.workflow.engine import Step, StepContext, StepResult, TaskEngine
from app.workflow.states import TaskStatus
from tests.runtime_fakes import REGISTRY, RecordingBus, agent_input
from tests.security.test_runtime_gate import record

OWNER = "w-engine-rt:" + "0001"  # at most 120 characters


async def ready_world(engine: AsyncEngine, settings: Settings, *, readiness: bool = True) -> None:
    """The registry synced into ``agent_profiles`` and, with ``readiness``, a recorded provider smoke so
    the gate lets a run through."""
    async with make_sessionmaker(engine)() as session:
        await sync_registry(session, REGISTRY, AuditWriter(Redactor()))
        await session.commit()
        if readiness:
            await record(session, settings)


def orchestrate(
    engine: AsyncEngine,
    settings: Settings,
    pricing: PricingTable,
    *,
    owner: str = OWNER,
    agent_steps: Mapping[str, AgentStep] | None = None,
    steps: Mapping[str, Step] | None = None,
    providers: ProviderFactory | None = None,
    tools: ToolExecutor | None = None,
    registry: AgentRegistry = REGISTRY,
    bus: RecordingBus | None = None,
) -> TaskEngine:
    """The production assembly with the test seams; the audit writer holds the settings' secret literals."""
    return build_orchestrator(
        sessions=make_sessionmaker(engine),
        bus=bus if bus is not None else RecordingBus(engine),
        audit=AuditWriter(Redactor(settings.secret_values())),
        settings=settings,
        worker_id=owner,
        registry=registry,
        pricing=pricing,
        steps=steps,
        agent_steps=agent_steps,
        providers=providers,
        tools=tools,
    )


def retime(orch: TaskEngine, **ports: float) -> TaskEngine:
    """The same engine with other timing ports (BC-5)."""
    return TaskEngine(dataclasses.replace(orch._ports, **ports))  # type: ignore[arg-type]


# --- build_input and on_success of the test steps -------------------------------------------------------


def _input_of(runtime_id: str) -> Any:
    async def build(ctx: StepContext) -> BaseModel:
        return agent_input(runtime_id, ctx.task_id)

    return build


intake_input = _input_of("A02")
plan_input = _input_of("A03")
implement_input = _input_of("B14")


async def to_planned(ctx: StepContext, success: AgentSuccess) -> StepResult:
    return StepResult.transition(TaskStatus.PLANNED, None)


async def to_validation_unavailable(ctx: StepContext, success: AgentSuccess) -> StepResult:
    return StepResult.transition(TaskStatus.INTERRUPTED, "validation_unavailable")


async def raw_done(ctx: StepContext) -> StepResult:
    """A plain engine step that is done (the ``context`` step of planning)."""
    return StepResult.done()


# --- observation ----------------------------------------------------------------------------------------


class Renewals:
    """Records ``(monotonic, statement)`` of every lease renewal (``UPDATE tasks SET lease_expires_at=``) on
    the engine's connections, not every UPDATE (BC-4). Use as a context manager so the listener is removed."""

    PREFIX = "UPDATE tasks SET lease_expires_at="

    def __init__(self, engine: AsyncEngine) -> None:
        self._target = engine.sync_engine
        self.calls: list[tuple[float, str]] = []

    def _listen(self, conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if statement.lstrip().startswith(self.PREFIX):
            self.calls.append((time.monotonic(), statement))

    def __enter__(self) -> Renewals:
        event.listen(self._target, "before_cursor_execute", self._listen)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self._target, "before_cursor_execute", self._listen)

    def after(self, moment: float) -> list[float]:
        return [at for at, _ in self.calls if at > moment]


# --- providers ------------------------------------------------------------------------------------------


class SwallowingProvider:
    """A provider that does not stop: it swallows every ``CancelledError`` and ignores the stop token until
    the test sets ``release``, then ends with ``LLMError("cancelled")``."""

    name = "scripted"

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.swallowed = 0

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        self.entered.set()
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.swallowed += 1
        raise LLMError("cancelled")

    async def aclose(self) -> None:
        return None


class StreamThenFail:
    """Streams a marker, then fails like a refusal; ``streamed`` records what reached ``on_text_delta``."""

    name = "scripted"

    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.streamed: list[str] = []

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        if on_text_delta is not None:
            await on_text_delta(self.marker)
            self.streamed.append(self.marker)
        raise LLMError("refusal")

    async def aclose(self) -> None:
        return None
