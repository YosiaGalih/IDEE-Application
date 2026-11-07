"""The tool contract (ARCHITECTURE §4.2; spec WP-3.1d Goal 1). Owned by AG-00.

Every runtime action of an agent goes through ``app.tools.gateway.ToolGateway`` (CLAUDE.md #2). A builtin is
a ``ToolSpec``: a Pydantic input model (``extra="forbid"``), a risk level, and a handler that receives a
read-only ``ToolContext``. What an agent may call comes from ``AgentGrant``, built from the validated
registry in memory (``app.agents.grants``), never from ``agent_profiles`` rows the runtime role could
UPDATE (3.1b AG-13 C3).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cancel import CancelToken

if TYPE_CHECKING:
    from app.workspace.fs import TaskWorkspace


class RiskLevel(IntEnum):
    """SECURITY_MODEL §3. R4 is never registered."""

    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3
    R4 = 4

    @classmethod
    def of(cls, catalog_risk: str) -> RiskLevel:
        """``"R0"`` … ``"R3"`` from the tool catalog."""
        return cls[catalog_risk]


class PermissionView(Protocol):
    """The permission profile fields a tool policy reads (TOOL_CATALOG "Permission profile"; v2 fields
    included). ``app.agents.registry.PermissionProfile`` satisfies it."""

    @property
    def filesystem(self) -> str: ...
    @property
    def sandbox(self) -> str: ...
    @property
    def git(self) -> str: ...
    @property
    def network(self) -> str: ...
    @property
    def deployment(self) -> str: ...
    @property
    def portfolio(self) -> str: ...
    @property
    def data_sensitivity_max(self) -> str: ...


def _frozen_quota() -> Mapping[str, int]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class AgentGrant:
    """What the validated registry allows one agent: its allowlist, permissions and per-tool quota."""

    agent_id: str
    tools: frozenset[str]
    permissions: PermissionView
    tool_quota: Mapping[str, int] = field(default_factory=_frozen_quota)


ToolStatus = Literal["ok", "error", "denied", "timeout", "approval_required", "cancelled"]


@dataclass(frozen=True, slots=True)
class Untrusted:
    """Where an output's content comes from when it is data the model must not obey (T1, SECURITY_MODEL
    §9): ``source`` is the tool, ``path`` the file or folder it read (untrusted text too, escaped by the
    gateway)."""

    source: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class ToolOutput:
    """What a handler returns: text or a JSON object for the model, and references to real evidence.
    ``untrusted``: the gateway wraps the content in ``<untrusted_content>`` after redaction and cutting."""

    content: str | Mapping[str, Any]
    evidence_refs: tuple[str, ...] = ()
    untrusted: Untrusted | None = None


class ToolError(Exception):
    """A failure a handler may show the model: ``code`` (short identifier) and a message without secrets,
    absolute paths or file contents. Any other exception reaches the model only as ``internal``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: uuid.UUID
    status: ToolStatus
    content: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Built by the runtime for one agent run; read-only for handlers.

    ``offered``: the tool names offered to the model in this request; a name the model sends that was not
    offered is refused even if the agent may hold it (T1). ``workspace``: the task worktree, the only file
    access a handler has (3.1d2).
    """

    task_id: uuid.UUID
    project_id: uuid.UUID
    agent_run_id: uuid.UUID | None
    grant: AgentGrant
    offered: frozenset[str]
    correlation_id: str
    cancel: CancelToken
    workspace: TaskWorkspace | None = None


Handler = Callable[[ToolContext, Any], Awaitable[ToolOutput]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One builtin. ``write_paths`` names the paths a write would touch (checked against ``test_only`` /
    ``docs_only`` before the handler runs). ``side_effect``: never run twice for one tool use."""

    name: str
    description: str
    input_model: type[BaseModel]
    risk: RiskLevel
    handler: Handler
    timeout_s: float = 60
    side_effect: bool = False
    write_paths: Callable[[Any], Iterable[str]] | None = None


class R3InputNotCanonical(ValueError):
    """The R3 input cannot be hashed canonically (floats, non-string keys): no approval can bind it."""


@dataclass(frozen=True, slots=True)
class R3Ticket:
    """``consumed``: an approval for exactly this action was approved and has just been used; otherwise
    ``approval_id`` is pending a person's decision."""

    approval_id: uuid.UUID
    consumed: bool


class R3Approvals(Protocol):
    """The approval side of an R3 tool call (ADR-022 port; implemented in ``app.services.tool_approvals``).
    Runs in the gateway's transaction, never commits. Raises ``R3InputNotCanonical`` for an input no approval
    can bind; any other exception is a server fault."""

    async def authorize(
        self,
        session: AsyncSession,
        *,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
        agent_id: str,
        payload: Mapping[str, object],
        summary: str,
    ) -> R3Ticket: ...


@dataclass(frozen=True, slots=True)
class SymbolRow:
    path: str
    line: int
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class SymbolRows:
    """Candidates from the project's symbol index (``repo/`` HEAD), before any agent policy is applied."""

    rows: list[SymbolRow]
    truncated: bool  # more candidates matched than were read
    index_commit: str | None  # None: the project was never indexed
    index_truncated: bool | None  # the index itself stopped at a limit


class SymbolIndex(Protocol):
    """Symbol search over the project index (ADR-022 port; implemented in ``app.services.repo_index``)."""

    async def candidates(self, project_id: uuid.UUID, name: str, kind: str | None) -> SymbolRows: ...


@dataclass(frozen=True, slots=True)
class ToolPorts:
    """Domain functions the gateway and builtins use without importing ``app.services`` (ADR-022)."""

    approvals: R3Approvals
    symbols: SymbolIndex | None = None
