"""Fake ports for the AgentRuntime (plan WP-3.1f2a Task 3 onwards).

Every fake appends to one shared ``log`` so a test can assert the order of the hooks: ``"beat"``, ``"gate"``,
``"budget"`` (before a model attempt), ``"budget:tool"`` (before a tool), ``"send"`` (a request reached the
provider), ``"record"`` (the ledger), ``"tool:<name>"``. The registry and the prompts are the real ones.

``refuse`` maps a 1-based call number to the exception that call raises, so a hook can refuse the first
attempt or only a retry.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.agents.prompt_book import PromptBook
from app.agents.registry import AgentRegistry, load_registry
from app.agents.runtime import (
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
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    TextBlock,
    TextDeltaHandler,
    ToolDef,
    ToolUseBlock,
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
        self.providers = FakeProviders(LoggedProvider(self.provider, self.log))

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
