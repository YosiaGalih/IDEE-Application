"""Fake ports for the AgentRuntime (plan WP-3.1f2a Task 3 onwards).

Every fake appends to one shared ``log`` so a test can assert the order of the hooks: ``"beat"``, ``"gate"``,
``"budget"`` (before a model attempt), ``"budget:tool"`` (before a tool), ``"send"`` (a request reached the
provider), ``"record"`` (the ledger), ``"tool:<name>"``. The registry and the prompts are the real ones.

``refuse`` maps a 1-based call number to the exception that call raises, so a hook can refuse the first
attempt or only a retry.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents import contracts as c
from app.agents.prompt_book import PromptBook
from app.agents.registry import AgentRegistry, AgentSpec, load_registry
from app.agents.runtime import (
    AgentRunRequest,
    AgentRuntime,
    ModelCall,
    RunEnd,
    RunScope,
    RunStart,
    RuntimeConfig,
    RuntimePorts,
    RunUsage,
)
from app.core.cancel import CancelToken
from app.core.redaction import Redactor
from app.llm.base import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    TextBlock,
    TextDeltaHandler,
    ToolDef,
    ToolUseBlock,
    Usage,
)
from app.llm.retry import AttemptHandler, AttemptRecord
from app.llm.scenarios.model import Fail, Reply, Scenario, Turn
from app.llm.scripted_provider import ScriptedProvider
from app.tools.base import AgentGrant, ToolContext, ToolResult
from app.tools.catalog import TOOL_CATALOG

BACKEND = Path(__file__).resolve().parents[1]
REGISTRY: AgentRegistry = load_registry(BACKEND.parent / "agents" / "registry.v2.yaml")
PROMPTS = PromptBook.load()
MODEL = "claude-haiku-5-5"
MODELS: Mapping[str, str] = {"fast": MODEL, "default": MODEL, "complex": MODEL}
SCENARIO = "test"


async def no_sleep(_: float) -> None:
    return None


class _Counted:
    def __init__(self, log: list[str], name: str, refuse: Mapping[int, BaseException] | None = None) -> None:
        self.log = log
        self.name = name
        self.calls = 0
        self.refuse = dict(refuse or {})

    def tick(self) -> None:
        self.calls += 1
        self.log.append(self.name)
        error = self.refuse.get(self.calls)
        if error is not None:
            raise error


class FakeGate(_Counted):
    def __init__(self, log: list[str], refuse: Mapping[int, BaseException] | None = None) -> None:
        super().__init__(log, "gate", refuse)
        self.models: Mapping[str, str] = MODELS

    async def check(self, project_id: uuid.UUID) -> Mapping[str, str]:
        self.tick()
        return self.models


class FakeHeartbeat(_Counted):
    def __init__(self, log: list[str], refuse: Mapping[int, BaseException] | None = None) -> None:
        super().__init__(log, "beat", refuse)

    async def beat(self, task_id: uuid.UUID) -> None:
        self.tick()


class FakeBudget:
    def __init__(
        self,
        log: list[str],
        refuse: Mapping[int, BaseException] | None = None,
        refuse_tool: Mapping[int, BaseException] | None = None,
    ) -> None:
        self.attempts = _Counted(log, "budget", refuse)
        self.tools = _Counted(log, "budget:tool", refuse_tool)
        self.calls: list[ModelCall] = []

    async def before_model_attempt(self, call: ModelCall) -> None:
        self.calls.append(call)
        self.attempts.tick()

    async def before_tool(self, scope: RunScope) -> None:
        self.tools.tick()


class FakeLedger:
    def __init__(self, log: list[str], refuse: Mapping[int, BaseException] | None = None) -> None:
        self.counter = _Counted(log, "record", refuse)
        self.records: list[tuple[ModelCall, AttemptRecord]] = []

    def on_attempt(self, call: ModelCall) -> AttemptHandler:
        async def record(attempt: AttemptRecord) -> None:
            self.records.append((call, attempt))
            self.counter.tick()

        return record


ZERO_USAGE = RunUsage(0, 0, 0, 0, Decimal(0), "estimated")


class FakeRuns:
    def __init__(self) -> None:
        self.starts: list[RunStart] = []
        self.finishes: list[tuple[uuid.UUID, RunEnd]] = []
        self.ids: list[uuid.UUID] = []

    async def start(self, run: RunStart) -> uuid.UUID:
        self.starts.append(run)
        run_id = uuid.uuid4()
        self.ids.append(run_id)
        return run_id

    async def finish(self, agent_run_id: uuid.UUID, end: RunEnd) -> RunUsage:
        self.finishes.append((agent_run_id, end))
        return ZERO_USAGE


class FakeEvents:
    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.usage_updates = 0

    async def delta(self, scope: RunScope, text: str) -> None:
        self.deltas.append(text)

    async def usage_updated(self, scope: RunScope) -> None:
        self.usage_updates += 1


ToolHandler = Callable[[ToolContext, object], Awaitable[ToolResult]]


def ok_result(content: str = "fake output") -> ToolResult:
    return ToolResult(uuid.uuid4(), "ok", content, False)


class FakeTools:
    """``tool_defs_for``: the grant ∩ the tools the catalog says are built, sorted (like the gateway)."""

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.handlers: dict[str, ToolHandler] = {}
        self.executed: list[tuple[ToolContext, str, object, str | None]] = []

    def tool_defs_for(self, grant: AgentGrant) -> list[ToolDef]:
        built = sorted(t for t in grant.tools if t in TOOL_CATALOG and TOOL_CATALOG[t].implemented)
        return [ToolDef(name, f"fake {name}", {"type": "object"}) for name in built]

    async def execute(
        self, ctx: ToolContext, tool_name: str, raw_input: object, *, tool_use_id: str | None = None
    ) -> ToolResult:
        self.log.append(f"tool:{tool_name}")
        self.executed.append((ctx, tool_name, raw_input, tool_use_id))
        handler = self.handlers.get(tool_name)
        return await handler(ctx, raw_input) if handler is not None else ok_result()


class LoggedProvider:
    """Logs ``send`` for every request that reaches the provider, then delegates."""

    def __init__(self, inner: LLMProvider, log: list[str]) -> None:
        self.inner = inner
        self.log = log
        self.name = inner.name

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        self.log.append("send")
        return await self.inner.create_message(req, on_text_delta=on_text_delta, cancel=cancel)


class SlowProvider:
    """Answers after ``delay`` seconds of wall clock; a cancel during the wait ends the attempt like a stream
    cut short (``LLMError("cancelled")`` with the prompt's usage and no output)."""

    def __init__(self, inner: LLMProvider, delay: float) -> None:
        self.inner = inner
        self.delay = delay
        self.name = inner.name

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse:
        if cancel is not None:
            try:
                await asyncio.wait_for(cancel.wait(), timeout=self.delay)
            except TimeoutError:
                pass
            else:
                raise LLMError("cancelled", request_id="slow", usage=Usage(input_tokens=10, output_tokens=0))
        else:
            await asyncio.sleep(self.delay)
        return await self.inner.create_message(req, on_text_delta=on_text_delta, cancel=cancel)


class FakeProviders:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def _open(self) -> AsyncIterator[LLMProvider]:
        self.opened += 1
        try:
            yield self.provider
        finally:
            self.closed += 1

    def open(self) -> Any:
        return self._open()


# --- scripts ------------------------------------------------------------------------------------------------


def text_turn(text: str = "Working on it.") -> Reply:
    return Reply((TextBlock(text),))


def tool_turn(name: str, tool_input: dict[str, Any] | None = None, tool_use_id: str = "toolu_1") -> Reply:
    return Reply((ToolUseBlock(tool_use_id, name, tool_input or {}),), stop_reason="tool_use")


def submit_turn(output: dict[str, Any], tool_use_id: str = "toolu_submit") -> Reply:
    return Reply((ToolUseBlock(tool_use_id, "submit_result", output),), stop_reason="tool_use")


def scenario(*turns: Turn) -> Scenario:
    return Scenario(name=SCENARIO, default=turns)


def in_order(*outcomes: Reply | Fail) -> Turn:
    """One turn answering successive requests in order (retries included), the last one repeated."""
    queue: Iterator[Reply | Fail] = iter(outcomes)
    last: list[Reply | Fail] = [outcomes[-1]]

    def answer(req: LLMRequest) -> Reply | Fail:
        last[0] = next(queue, last[0])
        return last[0]

    return answer


def scripted(*turns: Turn, delta_size: int = 16) -> ScriptedProvider:
    return ScriptedProvider(
        {SCENARIO: scenario(*turns)}, default_scenario=SCENARIO, delta_size=delta_size, record_requests=True
    )


# --- assembly -----------------------------------------------------------------------------------------------


@dataclass
class Fakes:
    provider: ScriptedProvider
    slow: float = 0.0  # wall-clock seconds each attempt takes (SlowProvider)
    log: list[str] = field(default_factory=list)
    gate: FakeGate = field(init=False)
    heartbeat: FakeHeartbeat = field(init=False)
    budget: FakeBudget = field(init=False)
    ledger: FakeLedger = field(init=False)
    runs: FakeRuns = field(init=False)
    events: FakeEvents = field(init=False)
    tools: FakeTools = field(init=False)
    providers: FakeProviders = field(init=False)

    def __post_init__(self) -> None:
        self.gate = FakeGate(self.log)
        self.heartbeat = FakeHeartbeat(self.log)
        self.budget = FakeBudget(self.log)
        self.ledger = FakeLedger(self.log)
        self.runs = FakeRuns()
        self.events = FakeEvents()
        self.tools = FakeTools(self.log)
        inner: LLMProvider = SlowProvider(self.provider, self.slow) if self.slow else self.provider
        self.providers = FakeProviders(LoggedProvider(inner, self.log))

    def ports(self) -> RuntimePorts:
        return RuntimePorts(
            gate=self.gate,
            budget=self.budget,
            ledger=self.ledger,
            runs=self.runs,
            events=self.events,
            tools=self.tools,
            heartbeat=self.heartbeat,
            providers=self.providers,
        )


def config(**overrides: Any) -> RuntimeConfig:
    values: dict[str, Any] = {
        "max_tool_iterations": 40,
        "max_output_tokens": 8192,
        "request_timeout_s": 120.0,
        "scripted": True,
    }
    values.update(overrides)
    return RuntimeConfig(**values)


def make_runtime(
    fakes: Fakes,
    *,
    runtime_config: RuntimeConfig | None = None,
    registry: AgentRegistry = REGISTRY,
    redactor: Redactor | None = None,
    **kwargs: Any,
) -> AgentRuntime:
    kwargs.setdefault("sleep", no_sleep)
    return AgentRuntime(
        registry=registry,
        prompts=PROMPTS,
        ports=fakes.ports(),
        config=runtime_config or config(),
        redactor=redactor or Redactor(),
        **kwargs,
    )


def a_scope(agent_id: str = "B14", **overrides: Any) -> RunScope:
    values: dict[str, Any] = {
        "project_id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "agent_run_id": uuid.uuid4(),
        "agent_id": agent_id,
    }
    values.update(overrides)
    return RunScope(**values)


def a_model_call(scope: RunScope | None = None, *, max_tokens: int = 1024, **request: Any) -> ModelCall:
    """A ``ModelCall`` whose request carries its call id, as the runtime builds it."""
    scope = scope or a_scope()
    call_id = uuid.uuid4()
    values: dict[str, Any] = {
        "model": MODEL,
        "system": "You are a test agent.",
        "messages": (Message("user", "hi"),),
        "max_tokens": max_tokens,
        "metadata": {"call_id": str(call_id), "agent_id": scope.agent_id},
    }
    values.update(request)
    return ModelCall(request=LLMRequest(**values), call_id=call_id, scope=scope)


class FakeClock:
    """``time.monotonic`` stand-in a test moves forward."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def registry_with(agent_id: str, **changes: Any) -> AgentRegistry:
    """The real registry with one entry changed, unvalidated (``model_copy``): test tools, short limits."""
    agents: list[AgentSpec] = []
    for agent in REGISTRY.agents:
        if agent.agent_id == agent_id:
            update_: dict[str, Any] = dict(changes)
            if "limits" in update_ and isinstance(update_["limits"], dict):
                update_["limits"] = agent.limits.model_copy(update=update_["limits"])
            agent = agent.model_copy(update=update_)
        agents.append(agent)
    return AgentRegistry(agents=tuple(agents))


# --- contract examples --------------------------------------------------------------------------------------

UUID_A = "123e4567-e89b-42d3-a456-426614174000"
CRITERION = c.Criterion(key="AC-1", criterion="It adds.", verification_method="test", mandatory=True)
REQUIREMENT = c.Requirement(
    key="REQ-1", title="Add", description="Add two numbers.", priority="must", acceptance_criteria=[CRITERION]
)
STEP = c.PlanStep(
    id="P1",
    title="Implement add",
    specialist="B14",
    description="Write add().",
    depends_on=[],
    requirement_keys=["REQ-1"],
    files_hint=["src/calc.py"],
)


def agent_input(runtime_id: str, task_id: uuid.UUID | None = None) -> BaseModel:
    """A valid input of the runtime's contract."""
    task = task_id or uuid.uuid4()
    inputs: dict[str, BaseModel] = {
        "A02": c.IntakeInput(task_id=task, prompt="Add a calculator."),
        "A03": c.PlanInput(task_id=task, workflow_type="create", requirements=[REQUIREMENT]),
        "B14": c.ImplementInput(task_id=task, step=STEP, requirements=[REQUIREMENT]),
        "C20": c.DebugInput(
            task_id=task,
            failure=c.FailureReport(category="assertion", summary="add(1, 2) returned 4"),
            attempt=1,
            requirements=[],
        ),
        "D22": c.ReviewInput(task_id=task, diff_ref=f"diff:{task}", changed_files=[], requirements=[]),
        "A07": c.UserProxyInput(task_id=task, baseline_version=1, criteria=[], evidence=[]),
    }
    return inputs[runtime_id]


OUTPUTS: Mapping[str, dict[str, Any]] = {
    "IntakeOutput": {
        "title": "Add a calculator",
        "summary": "A calculator that adds.",
        "workflow_type": "create",
        "complexity": "low",
        "risk": "low",
        "requirements": [REQUIREMENT.model_dump(mode="json")],
        "constraints": [],
        "detected_stack_hints": [],
        "clarification_questions": [],
    },
    "PlanOutput": {
        "steps": [STEP.model_dump(mode="json")],
        "test_plan": {"summary": "Unit tests.", "criterion_keys": ["AC-1"]},
        "risks": [],
    },
    "ImplementOutput": {
        "summary": "Nothing changed.",
        "changed_files": [],
        "evidence_refs": [],
        "follow_ups": [],
    },
    "DebugOutput": {
        "root_cause": "Off by one.",
        "category": "assertion",
        "fix_summary": "Fixed add().",
        "changed_files": [],
        "next_step": "retest",
        "evidence_refs": [],
    },
    "ReviewOutput": {"summary": "No findings.", "findings": []},
    "UserProxyOutput": {
        "status": "unverified",
        "summary": "Nothing verified.",
        "criteria_results": [],
        "next_actions": [],
        "confidence": "low",
    },
}


def run_request(
    agent_id: str = "B14",
    *,
    runtime_id: str | None = None,
    task_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    cancel: CancelToken | None = None,
    **overrides: Any,
) -> AgentRunRequest:
    task = task_id or uuid.uuid4()
    values: dict[str, Any] = {
        "agent_id": agent_id,
        "task_id": task,
        "project_id": project_id or uuid.uuid4(),
        "agent_input": agent_input(runtime_id or agent_id, task),
        "cancel": cancel or CancelToken(),
    }
    values.update(overrides)
    return AgentRunRequest(**values)


# --- a real composition for the integration tests -----------------------------------------------------------

WORKER_ID = "worker-test-1"


async def hold_lease(session: AsyncSession, task_id: uuid.UUID, worker_id: str = WORKER_ID) -> None:
    """The registry synced (``agent_runs.agent_id`` points at ``agent_profiles``) and the task's lease held by
    ``worker_id``, as the job that runs a task holds it."""
    from sqlalchemy import text

    from app.core.audit import AuditWriter
    from app.services.agents import sync_registry

    await sync_registry(session, REGISTRY, AuditWriter(Redactor()))
    await session.execute(
        text(
            "UPDATE tasks SET lease_owner = :worker,"
            " lease_expires_at = clock_timestamp() + interval '60 seconds' WHERE id = :id"
        ),
        {"worker": worker_id, "id": task_id},
    )
    await session.commit()


class RecordingBus:
    """An ``EventBus`` that keeps every message. With ``engine``, each stored event (one with a seq) is looked
    up from another connection as it is published: a publish before commit fails the test."""

    def __init__(self, engine: Any = None) -> None:
        self.messages: list[dict[str, Any]] = []
        self.engine = engine
        self.unseen: list[dict[str, Any]] = []

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        self.messages.append(message)
        if self.engine is not None and message["seq"] is not None:
            from sqlalchemy import text

            async with self.engine.connect() as conn:
                found = await conn.scalar(
                    text("SELECT count(*) FROM task_events WHERE task_id = :t AND seq = :s"),
                    {"t": uuid.UUID(message["task_id"]), "s": message["seq"]},
                )
            if not found:
                self.unseen.append(message)

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [m for m in self.messages if m["type"] == event_type]


@dataclass
class Composition:
    runtime: AgentRuntime
    provider: ScriptedProvider
    runs: Any  # AgentRunWriter
    events: Any
    providers: FakeProviders


def compose(
    sessions: async_sessionmaker[AsyncSession],
    settings: Any,
    pricing: Any,
    provider: ScriptedProvider,
    *,
    tools: Any,
    registry: AgentRegistry = REGISTRY,
    worker_id: str = WORKER_ID,
    redactor: Redactor | None = None,
    events: Any = None,
    bus: Any = None,
) -> Composition:
    """The production ports over a real database; the events are ``TaskRunEvents`` over ``bus`` when one is
    given, else ``events`` or ``FakeEvents``; the provider is the given scripted one. ``build_agent_runtime``
    is the same assembly with the production provider factory and sleep."""
    from app.core.audit import AuditWriter
    from app.services.agent_runtime import (
        AgentRunWriter,
        DbProviderGate,
        GuardBudget,
        LeaseHeartbeat,
        LedgerRecorder,
        TaskRunEvents,
    )
    from app.services.usage import UsageLedger
    from app.workflow.budget import BudgetDefaults, BudgetGuard

    redactor = redactor or Redactor(settings.secret_values())
    runs = AgentRunWriter(sessions, bus, AuditWriter(redactor))
    if events is None:
        events = TaskRunEvents(sessions, bus, redactor) if bus is not None else FakeEvents()
    providers = FakeProviders(provider)
    ports = RuntimePorts(
        gate=DbProviderGate(sessions, settings),
        budget=GuardBudget(BudgetGuard(sessions, pricing, BudgetDefaults.from_settings(settings))),
        ledger=LedgerRecorder(UsageLedger(sessions, pricing)),
        runs=runs,
        events=events,
        tools=tools,
        heartbeat=LeaseHeartbeat(sessions, worker_id),
        providers=providers,
    )
    runtime = AgentRuntime(
        registry=registry,
        prompts=PROMPTS,
        ports=ports,
        config=config(scripted=settings.llm_provider == "scripted"),
        redactor=redactor,
        sleep=no_sleep,
    )
    return Composition(runtime, provider, runs, events, providers)
