"""Test tools for the ToolGateway (spec WP-3.1d): a catalog with test entries, specs whose handlers record
their calls, and the context and gateway builders. No builtin exists before 3.1d2, and no R3 tool is live in
the MVP, so the gateway is exercised with these."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.registry import PermissionProfile
from app.core.audit import AuditWriter
from app.core.cancel import CancelToken
from app.core.redaction import Redactor
from app.services.tool_gateway import build_tool_gateway
from app.tools.base import AgentGrant, RiskLevel, ToolContext, ToolOutput, ToolSpec
from app.tools.catalog import TOOL_CATALOG, ToolInfo
from app.tools.gateway import ToolGateway

SECRET = "kk-test-secret-literal-0123456789"
REDACTOR = Redactor([SECRET])
AUDIT = AuditWriter(REDACTOR)

CATALOG = MappingProxyType(
    {
        **TOOL_CATALOG,
        "test.read": ToolInfo("test.read", "R0", "backend", ("fs_read",), "mvp"),
        "test.write": ToolInfo("test.write", "R1", "backend", ("fs_write",), "mvp"),
        "test.exec": ToolInfo("test.exec", "R2", "sandbox", (), "mvp"),
        "test.r3": ToolInfo("test.r3", "R3", "worker", (), "mvp"),
        "test.planned": ToolInfo("test.planned", "R0", "backend", (), "planned"),
    }
)


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = "README.md"
    text: str = ""


class WriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    content: str = ""


class R3Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str
    count: int = 1


@dataclass
class Recorder:
    """A handler that records each call and returns (or raises, or waits) as told."""

    result: Callable[[Any], ToolOutput] = field(default=lambda _: ToolOutput("done"))
    delay: float = 0
    calls: list[Any] = field(default_factory=list[Any])
    started: asyncio.Event = field(default_factory=asyncio.Event)

    async def __call__(self, ctx: ToolContext, validated: Any) -> ToolOutput:
        self.calls.append(validated)
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result(validated)


def spec(
    name: str, handler: Callable[[ToolContext, Any], Awaitable[ToolOutput]], **overrides: Any
) -> ToolSpec:
    defaults: dict[str, dict[str, Any]] = {
        "test.read": {"input_model": ReadInput, "risk": RiskLevel.R0},
        "test.write": {
            "input_model": WriteInput,
            "risk": RiskLevel.R1,
            "side_effect": True,
            "write_paths": lambda v: [v.path],
        },
        "test.exec": {"input_model": ReadInput, "risk": RiskLevel.R2},
        "test.r3": {"input_model": R3Input, "risk": RiskLevel.R3, "side_effect": True},
    }
    values: dict[str, Any] = {"description": f"{name} for tests", **defaults.get(name, {}), **overrides}
    return ToolSpec(name=name, handler=handler, **values)


def permissions(**overrides: str) -> PermissionProfile:
    values = {
        "filesystem": "scoped_write",
        "sandbox": "allow",
        "git": "branch_commit",
        "network": "deny",
        "deployment": "deny",
        "approval_grant": "deny",
    }
    values.update(overrides)
    return PermissionProfile.model_validate(values)


def grant(tools: Iterable[str], *, quota: dict[str, int] | None = None, **perms: str) -> AgentGrant:
    return AgentGrant(
        agent_id="B14",
        tools=frozenset(tools),
        permissions=permissions(**perms),
        tool_quota=MappingProxyType(quota or {}),
    )


def context(
    *,
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    tools: Iterable[str],
    offered: Iterable[str] | None = None,
    agent_run_id: uuid.UUID | None = None,
    cancel: CancelToken | None = None,
    quota: dict[str, int] | None = None,
    perms: dict[str, str] | None = None,
    **more_perms: str,
) -> ToolContext:
    tools = list(tools)
    return ToolContext(
        task_id=task_id,
        project_id=project_id,
        agent_run_id=agent_run_id,
        grant=grant(tools, quota=quota, **(perms or {}), **more_perms),
        offered=frozenset(tools if offered is None else offered),
        correlation_id=f"task:{task_id}",
        cancel=cancel or CancelToken(),
    )


def gateway(sessions: async_sessionmaker[AsyncSession], *specs: ToolSpec) -> ToolGateway:
    # The test catalog adds test tools; everything else is the composition of production.
    return build_tool_gateway(sessions=sessions, audit=AUDIT, specs=specs, catalog=CATALOG)
