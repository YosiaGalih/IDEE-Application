"""Risk policy of the tool catalog (SECURITY_MODEL T7, §3): R4 actions are never registered.

The gateway's own policy checks arrive with the ToolGateway (WP-3.1d); this pins the catalog it will read.
"""

from __future__ import annotations

import re

from app.tools.catalog import R3_OWNERS, TOOL_CATALOG

# Force push, host shell, Docker, approving, and changing audit or policy (SECURITY_MODEL §3, R4).
_R4_NAME = re.compile(r"force|shell|docker|exec_host|approve|audit|policy|sudo", re.IGNORECASE)


def test_r4_never_registered() -> None:
    assert {tool.risk for tool in TOOL_CATALOG.values()} <= {"-", "R0", "R1", "R2", "R3"}
    assert [name for name in TOOL_CATALOG if _R4_NAME.search(name)] == []


def test_r3_tools_are_not_live_and_have_owners() -> None:
    r3 = {name: tool for name, tool in TOOL_CATALOG.items() if tool.risk == "R3"}
    assert r3, "the catalog lists R3 tools"
    assert all(tool.status in {"disabled", "planned"} for tool in r3.values()), r3
    assert all(not tool.implemented for tool in r3.values())
    assert set(R3_OWNERS) <= set(r3)
