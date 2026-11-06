"""Risk policy of the registry and the tool catalog (SECURITY_MODEL §3, T7; AC-10).

The gateway's own policy checks arrive with the ToolGateway (WP-3.1d); this pins what it will read.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from app.agents.registry import RegistryError, load_registry, parse_registry
from app.tools.catalog import R3_OWNERS, TOOL_CATALOG

REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"

# R4, never registered (SECURITY_MODEL §3, T19): host shell or terminal, Docker, reading .env/keys/secrets,
# changing audit or policy, creating or approving approvals, direct DB access, admin keys, SSO/SCIM,
# force-push.
R4_PREFIXES = (
    "shell.",
    "terminal.",
    "host.",
    "docker.",
    "audit.",
    "policy.",
    "approval.",
    "approvals.",
    "db.",
    "sql.",
    "admin.",
    "sso.",
    "scim.",
    "secrets.",
)
R4_NAMES = {"env.read", "env.read_file", "git.force_push", "git.push_force", "github.force_push"}
R4_FRAGMENTS = ("force_push", "approve", "exec_host", "sudo", "read_secret", "read_env", "read_key")


def test_r4_never_registered() -> None:
    assert {tool.risk for tool in TOOL_CATALOG.values()} <= {"-", "R0", "R1", "R2", "R3"}
    forbidden = [
        name
        for name in TOOL_CATALOG
        if name.startswith(R4_PREFIXES) or name in R4_NAMES or any(f in name for f in R4_FRAGMENTS)
    ]
    assert forbidden == []


def test_r3_tools_are_not_live_and_have_owners() -> None:
    r3 = {name: tool for name, tool in TOOL_CATALOG.items() if tool.risk == "R3"}
    assert r3, "the catalog lists R3 tools"
    assert all(tool.status in {"disabled", "planned"} for tool in r3.values()), r3
    assert all(not tool.implemented for tool in r3.values())
    assert set(R3_OWNERS) <= set(r3)


def test_reviewer_profile_read_only() -> None:
    # AC-10: the default reviewer reads; it never writes, commits or executes.
    d22 = load_registry(REGISTRY).by_id("D22")
    assert d22 is not None
    perms = d22.permissions
    assert (perms.filesystem, perms.sandbox, perms.git, perms.deployment, perms.approval_grant) == (
        "read_only",
        "deny",
        "read",
        "deny",
        "deny",
    )
    writes = [t for t in d22.tools if "fs_write" in TOOL_CATALOG[t].preconditions]
    assert writes == []


@pytest.mark.parametrize(
    ("field", "value", "tool"),
    [("filesystem", "scoped_write", "code.write_file"), ("git", "branch_commit", "git.commit_task")],
)
def test_a_reviewer_given_write_or_commit_is_refused(field: str, value: str, tool: str) -> None:
    data = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    d22 = next(a for a in data["agents"] if a["agent_id"] == "D22")
    d22["permissions"][field] = value
    d22["tools"].append(tool)
    with pytest.raises(RegistryError) as raised:
        parse_registry(copy.deepcopy(data))
    assert f"D22: permissions.{field} must be" in "; ".join(raised.value.problems)
