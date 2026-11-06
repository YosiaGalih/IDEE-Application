"""Building blocks of a scripted scenario (ADR-015)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.llm.base import ErrorKind, LLMRequest, StopReason, TextBlock, ToolUseBlock, Usage

DEFAULT_USAGE = Usage(input_tokens=100, output_tokens=20)


@dataclass(frozen=True, slots=True)
class Reply:
    content: tuple[TextBlock | ToolUseBlock, ...]
    stop_reason: StopReason = "end_turn"
    usage: Usage = DEFAULT_USAGE


@dataclass(frozen=True, slots=True)
class Fail:
    kind: ErrorKind
    retry_after_s: float | None = None
    usage: Usage | None = None


Turn = Reply | Fail | Callable[[LLMRequest], Reply | Fail]


@dataclass(frozen=True, slots=True)
class Scenario:
    """Turns per agent (``scripts`` keyed by agent id), falling back to ``default``.

    Turn n answers a request whose history holds n assistant messages, so a scenario carries no state: a run
    resumed after a worker restart gets the same answers. A failing turn therefore fails on every retry too.
    """

    name: str
    default: tuple[Turn, ...]
    scripts: Mapping[str, tuple[Turn, ...]] = field(default_factory=dict)
