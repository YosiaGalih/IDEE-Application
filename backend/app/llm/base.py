"""The LLM provider port (ARCHITECTURE §4.2). Owned by AG-00; adapters translate to and from it.

One ``create_message`` call is one attempt against the provider. Retrying is the job of ``app.llm.retry``, so
that every attempt (with its own request id, tokens and duration) can be recorded (FR-133).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args

from app.core.cancel import CancelToken

# Canonical tool names keep their dots (ADR-023): lower-case segments joined by '.', never '__' (the adapter's
# encoding of '.'), and at most 64 characters once encoded, the provider's limit.
_TOOL_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*(?:\.[a-z][a-z0-9]*(?:_[a-z0-9]+)*)*")
_MAX_ENCODED_TOOL_NAME = 64


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["user", "assistant"]
    content: str | tuple[ContentBlock, ...]


@dataclass(frozen=True, slots=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]

    def __post_init__(self) -> None:
        encoded_length = len(self.name) + self.name.count(".")
        if not _TOOL_NAME.fullmatch(self.name) or encoded_length > _MAX_ENCODED_TOOL_NAME:
            raise ValueError(f"invalid tool name {self.name!r}")


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    def __post_init__(self) -> None:
        counts = (self.input_tokens, self.output_tokens, self.cache_creation_tokens, self.cache_read_tokens)
        if any(count < 0 for count in counts):
            raise ValueError("token counts cannot be negative")


@dataclass(frozen=True, slots=True)
class LLMRequest:
    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolDef, ...] = ()
    max_tokens: int = 8192
    # KodeKantor-side context (task_id, agent_id, agent_run_id, scenario); never sent to the provider.
    metadata: Mapping[str, str] = field(default_factory=dict)
    cache_system: bool = True
    timeout_s: float = 120.0


StopReason = Literal["end_turn", "tool_use", "stop_sequence"]


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: tuple[TextBlock | ToolUseBlock, ...]
    stop_reason: StopReason
    usage: Usage | None
    model: str
    request_id: str | None
    duration_ms: int


ErrorKind = Literal[
    "rate_limited",
    "overloaded",
    "transient",
    "timeout",
    "refusal",
    "bad_request",
    "auth",
    "malformed",
    "cancelled",
    "max_tokens",
]
ERROR_KINDS: frozenset[str] = frozenset(get_args(ErrorKind))
RETRYABLE: frozenset[str] = frozenset({"rate_limited", "overloaded", "transient", "timeout"})


class LLMError(Exception):
    """A provider failure, normalised. The message names the kind, HTTP status and vendor error type only:
    never a request or response body, which can carry prompt text or echo a key."""

    def __init__(
        self,
        kind: ErrorKind,
        *,
        retry_after_s: float | None = None,
        request_id: str | None = None,
        usage: Usage | None = None,
        status_code: int | None = None,
        vendor_type: str | None = None,
    ) -> None:
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown LLM error kind {kind!r}")
        details = [f"HTTP {status_code}"] if status_code is not None else []
        if vendor_type:
            details.append(vendor_type)
        super().__init__(f"{kind} ({', '.join(details)})" if details else kind)
        self.kind: ErrorKind = kind
        self.retry_after_s = retry_after_s
        self.request_id = request_id
        self.usage = usage
        self.status_code = status_code
        self.vendor_type = vendor_type

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


TextDeltaHandler = Callable[[str], Awaitable[None]]


class LLMProvider(Protocol):
    name: str

    async def create_message(
        self,
        req: LLMRequest,
        *,
        on_text_delta: TextDeltaHandler | None = None,
        cancel: CancelToken | None = None,
    ) -> LLMResponse: ...
