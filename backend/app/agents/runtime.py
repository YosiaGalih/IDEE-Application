"""The AgentRuntime (ARCHITECTURE §4.2; spec WP-3.1f Keputusan Lead #15-#35; plan WP-3.1f2a).

One agent run: the model is called through one path, ``AgentRuntime._call_model``, the only
``create_with_retry`` in app/ (Keputusan Lead #17; tests/unit/test_model_call_guard.py). Before every
attempt, the first and each retry, ``before_attempt`` renews the task lease, reads the provider gate afresh
and checks the budget; after every attempt that reached the provider, the ledger records it, and a ledger
failure fails the call (Keputusan Lead #8, #20).

The runtime reaches the database, providers, events and tools only through the ports below (ADR-022); the
adapters live in ``app.services.agent_runtime``. It holds no database session, ends agent runs and never
moves a task (Keputusan Lead #15): two import-linter contracts keep both true.

Nothing here logs a ``Message``, an ``LLMRequest`` or a tool result (their repr carries content): log lines
carry ids and type names only.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from pydantic import BaseModel

from app.agents.prompt_book import PromptBook
from app.agents.registry import AgentRegistry
from app.core.cancel import CancelToken
from app.core.logging import get_logger
from app.core.redaction import Redactor
from app.llm.base import (
    ContentBlock,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    TextBlock,
    TextDeltaHandler,
    ToolDef,
    ToolUseBlock,
)
from app.llm.retry import AttemptHandler, AttemptRecord, RestartHandler, RetryPolicy, create_with_retry
from app.tools.base import AgentGrant, ToolContext, ToolResult

if TYPE_CHECKING:
    from app.workspace.fs import TaskWorkspace

SUBMIT_RESULT: Final = "submit_result"  # == app.tools.gateway.TERMINAL_TOOL (test)
HEARTBEAT_INTERVAL_S: Final = 20.0  # == app.workflow.lease.HEARTBEAT_SECONDS (test)

RunStatus = Literal["succeeded", "failed", "cancelled", "budget_exceeded", "approval_required"]
# == app.models.enums.AGENT_RUN_ERROR_CODES (test), in the same order.
RunErrorCode = Literal[
    "PROVIDER_DISABLED",
    "provider_not_ready",
    "provider_auth",
    "usage_unrecorded",
    "invalid_output",
    "iteration_limit",
    "timeout",
    "lease_lost",
    "model_error",
    "internal_error",
    "interrupted",
]
StopReason = Literal["cancelled", "timeout", "lease_lost"]

_log = get_logger(__name__)


# --- port exceptions: adapters translate domain errors into these (ADR-022) ---------------------------------


class ProviderBlocked(Exception):
    """The provider gate refused: ``PROVIDER_DISABLED`` (switched off, kill switch) or ``provider_not_ready``.
    ``reason`` is a short identifier (a readiness verdict reason, ``kill_switch``, ``project_missing``,
    ``no_provider``), never free text."""

    def __init__(self, code: Literal["PROVIDER_DISABLED", "provider_not_ready"], reason: str) -> None:
        super().__init__(f"{code} ({reason})")
        self.code: Literal["PROVIDER_DISABLED", "provider_not_ready"] = code
        self.reason = reason


class BudgetRefused(Exception):
    """The task budget refused the next attempt or tool (from ``BudgetExceeded``)."""

    def __init__(
        self,
        kind: str,
        limit: int | Decimal,
        used: int | Decimal | None,
        *,
        next_bound: int | Decimal | None,
    ) -> None:
        super().__init__(f"budget exceeded: {kind}")
        self.kind = kind
        self.limit = limit
        self.used = used
        self.next_bound = next_bound


class LeaseLostError(Exception):
    """The worker no longer holds the task lease: it must stop without sending anything."""


class UsageUnrecorded(Exception):
    """The ledger port failed to record an attempt: raised by the runtime, from the ledger's error."""


# --- the call -----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunScope:
    project_id: uuid.UUID
    task_id: uuid.UUID  # required (Keputusan Lead #17): checked at run time, not only by type
    agent_run_id: uuid.UUID
    agent_id: str

    def __post_init__(self) -> None:
        _require_task(self.task_id)


def _require_task(task_id: object) -> None:
    if not isinstance(task_id, uuid.UUID):
        raise TypeError("a model call needs the UUID of its task")


def _block(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


def request_size_bytes(req: LLMRequest) -> int:
    """UTF-8 size of compact JSON of system, every message block and the tool definitions.

    The input token bound of a call (Keputusan Lead #17): a byte-level BPE token covers at least one byte, and
    the JSON overhead (keys, quotes, every tool schema) outweighs the tool prompt a provider adds.
    """
    payload = {
        "system": req.system,
        "messages": [
            {
                "role": message.role,
                "content": message.content
                if isinstance(message.content, str)
                else [_block(block) for block in message.content],
            }
            for message in req.messages
        ],
        "tools": [
            {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
            for tool in req.tools
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return len(text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ModelCall:
    """One logical model call of a run. The budget bound and the ledger context are both built from it
    (``app.services.agent_runtime.bind_model_call``), so the two never disagree (AG-13 3.1f1 L1)."""

    request: LLMRequest
    call_id: uuid.UUID
    scope: RunScope
    input_token_bound: int = field(init=False)

    def __post_init__(self) -> None:
        _require_task(self.scope.task_id)
        # retry.py logs a lost ledger row under req.metadata["call_id"]: it must name this call.
        if self.request.metadata.get("call_id") != str(self.call_id):
            raise ValueError("the request metadata must carry this call's call_id")
        object.__setattr__(self, "input_token_bound", request_size_bytes(self.request))

    @property
    def model(self) -> str:
        return self.request.model

    @property
    def output_token_cap(self) -> int:
        return self.request.max_tokens


# --- results ------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunUsage:
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    estimated_cost: Decimal | None
    cost_status: str


@dataclass(frozen=True, slots=True)
class BudgetBreach:
    kind: str
    limit: int | Decimal
    used: int | Decimal | None
    next_bound: int | Decimal | None


@dataclass(frozen=True, slots=True)
class PendingTool:
    tool_use_id: str
    tool_name: str
    tool_call_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class RunStart:
    task_id: uuid.UUID
    project_id: uuid.UUID
    agent_id: str
    agent_version: int
    step: str
    model_provider: str | None  # None when the run is refused before a provider exists
    model_name: str | None
    prompt_version: str
    input: Mapping[str, Any]  # agent_input.model_dump(mode="json"); the store redacts


@dataclass(frozen=True, slots=True)
class RunEnd:
    status: RunStatus
    iterations: int
    error_code: RunErrorCode | None = None
    error: str | None = None  # short text; the store redacts and cuts it
    output: Mapping[str, Any] | None = None  # succeeded: the output; approval_required: the pending tool
    budget: BudgetBreach | None = None

    def __post_init__(self) -> None:
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("a failed run has an error code, and only a failed run")
        if (self.status == "budget_exceeded") != (self.budget is not None):
            raise ValueError("a budget breach comes with budget_exceeded, and only with it")
        if self.output is not None and self.status not in ("succeeded", "approval_required"):
            raise ValueError("only a succeeded or approval_required run has an output")


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    agent_id: str
    task_id: uuid.UUID
    project_id: uuid.UUID
    agent_input: BaseModel
    cancel: CancelToken
    workspace: TaskWorkspace | None = None
    scenario: str | None = None  # scripted only (Keputusan Lead #27)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    agent_run_id: uuid.UUID
    status: RunStatus
    output: BaseModel | None
    usage: RunUsage
    error_code: RunErrorCode | None
    error: str | None
    budget: BudgetBreach | None
    pending_tool: PendingTool | None


# --- ports --------------------------------------------------------------------------------------------------


class ProviderGate(Protocol):
    async def check(self, project_id: uuid.UUID) -> Mapping[str, str]:
        """The effective model per tier, or ``ProviderBlocked``."""
        ...


class BudgetCheck(Protocol):
    async def before_model_attempt(self, call: ModelCall) -> None: ...
    async def before_tool(self, scope: RunScope) -> None: ...


class UsageLedgerPort(Protocol):
    def on_attempt(self, call: ModelCall) -> AttemptHandler: ...


class RunStore(Protocol):
    async def start(self, run: RunStart) -> uuid.UUID: ...
    async def finish(self, agent_run_id: uuid.UUID, end: RunEnd) -> RunUsage: ...


class RunEvents(Protocol):
    async def delta(self, scope: RunScope, text: str) -> None: ...
    async def usage_updated(self, scope: RunScope) -> None: ...


class ToolExecutor(Protocol):
    def tool_defs_for(self, grant: AgentGrant) -> list[ToolDef]: ...
    async def execute(
        self, ctx: ToolContext, tool_name: str, raw_input: object, *, tool_use_id: str | None = None
    ) -> ToolResult: ...


class Heartbeat(Protocol):
    async def beat(self, task_id: uuid.UUID) -> None: ...


class ProviderFactory(Protocol):
    def open(self) -> AbstractAsyncContextManager[LLMProvider]: ...


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    gate: ProviderGate
    budget: BudgetCheck
    ledger: UsageLedgerPort
    runs: RunStore
    events: RunEvents
    tools: ToolExecutor
    heartbeat: Heartbeat
    providers: ProviderFactory


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    max_tool_iterations: int  # CLAUDE_MAX_TOOL_ITERATIONS
    max_output_tokens: int  # CLAUDE_MAX_OUTPUT_TOKENS
    request_timeout_s: float  # CLAUDE_REQUEST_TIMEOUT_SECONDS
    scripted: bool  # LLM_PROVIDER == "scripted"
    heartbeat_interval_s: float = HEARTBEAT_INTERVAL_S
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)


# --- the runtime --------------------------------------------------------------------------------------------


class AgentRuntime:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        prompts: PromptBook,
        ports: RuntimePorts,
        config: RuntimeConfig,
        redactor: Redactor,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._registry = registry
        self._prompts = prompts
        self._ports = ports
        self._config = config
        self._redactor = redactor
        self._clock = clock
        self._sleep = sleep

    async def _call_model(
        self,
        call: ModelCall,
        provider: LLMProvider,
        *,
        cancel: CancelToken,
        on_text_delta: TextDeltaHandler | None = None,
        on_restart: RestartHandler | None = None,
    ) -> LLMResponse:
        """The only create_with_retry in app/ (Keputusan Lead #17; tests/unit/test_model_call_guard.py).

        ``before_attempt`` = heartbeat -> provider gate -> budget (Keputusan plan #4): without the lease
        nothing may be touched, the gate is cheap policy, the budget reads the ledger. Any of them raising
        stops the call with nothing sent and nothing recorded. ``on_attempt`` = the ledger; its failure
        becomes ``UsageUnrecorded`` from it, so a paid answer that was not recorded never comes back
        (Keputusan Lead #8, #20). The run loop passes its stop token as ``cancel`` and the delta buffer as
        ``on_text_delta``/``on_restart``."""
        ports = self._ports
        scope = call.scope

        async def before_attempt(attempt: int) -> None:
            await ports.heartbeat.beat(scope.task_id)
            await ports.gate.check(scope.project_id)
            await ports.budget.before_model_attempt(call)

        record = ports.ledger.on_attempt(call)

        async def on_attempt(attempt: AttemptRecord) -> None:
            try:
                await record(attempt)
            except Exception as error:
                raise UsageUnrecorded(
                    f"attempt {attempt.attempt} of call {call.call_id} was not recorded"
                ) from error

        return await create_with_retry(
            provider,
            call.request,
            policy=self._config.retry_policy,
            on_text_delta=on_text_delta,
            on_restart=on_restart,
            cancel=cancel,
            on_attempt=on_attempt,
            before_attempt=before_attempt,
            sleep=self._sleep,
        )
