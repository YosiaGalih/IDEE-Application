"""Composition of the ToolGateway (ADR-022: the gateway reaches domain functions only through ``ToolPorts``).

Production registers the builtins of ``app.tools.builtin`` (WP-3.1d2a, 3.1d2b) with the symbol index port;
tests pass their own specs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.events import EventBus
from app.services.repo_index import RepoSymbolIndex
from app.services.tool_approvals import ToolR3Approvals
from app.tools.base import ToolPorts, ToolSpec
from app.tools.builtin import builtin_specs
from app.tools.catalog import TOOL_CATALOG, ToolInfo
from app.tools.gateway import ToolGateway


def build_tool_gateway(
    *,
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    bus: EventBus | None = None,
    specs: Iterable[ToolSpec] | None = None,
    catalog: Mapping[str, ToolInfo] = TOOL_CATALOG,
) -> ToolGateway:
    """``catalog``: replaced only by tests (the MVP has no live R3 tool to exercise the approval path)."""
    ports = ToolPorts(approvals=ToolR3Approvals(audit=audit), symbols=RepoSymbolIndex(sessions))
    gateway = ToolGateway(sessions=sessions, audit=audit, ports=ports, catalog=catalog, bus=bus)
    for spec in builtin_specs(ports.symbols) if specs is None else specs:
        gateway.register(spec)
    return gateway
