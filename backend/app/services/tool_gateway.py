"""Composition of the ToolGateway (ADR-022: the gateway reaches domain functions only through ``ToolPorts``).

3.1d1 registers no builtin; 3.1d2 passes its specs here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.events import EventBus
from app.services.tool_approvals import ToolR3Approvals
from app.tools.base import ToolPorts, ToolSpec
from app.tools.catalog import TOOL_CATALOG, ToolInfo
from app.tools.gateway import ToolGateway


def build_tool_gateway(
    *,
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    bus: EventBus | None = None,
    specs: Iterable[ToolSpec] = (),
    catalog: Mapping[str, ToolInfo] = TOOL_CATALOG,
) -> ToolGateway:
    """``catalog``: replaced only by tests (the MVP has no live R3 tool to exercise the approval path)."""
    gateway = ToolGateway(
        sessions=sessions,
        audit=audit,
        ports=ToolPorts(approvals=ToolR3Approvals(audit=audit)),
        catalog=catalog,
        bus=bus,
    )
    for spec in specs:
        gateway.register(spec)
    return gateway
