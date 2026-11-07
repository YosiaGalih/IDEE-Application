"""Risk policy of the registry, the tool catalog and the gateway (SECURITY_MODEL §3, T7; AC-06, AC-10).

The registry loader and the ToolGateway share ``app.tools.policy``; this pins the rules both enforce.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from app.agents.registry import RegistryError, load_registry, parse_registry
from app.tools.catalog import R3_OWNERS, TOOL_CATALOG
from app.tools.policy import R4_FRAGMENTS, R4_NAMES, R4_PREFIXES, is_r4, unmet_preconditions, write_allowed

REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"


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


# --- the gateway's policy (app/tools/policy.py; spec WP-3.1d Goal 2) --------------------------------------


@pytest.mark.parametrize(
    ("tool", "perms", "unmet"),
    [
        ("repo.read_file", {"filesystem": "none"}, ["fs_read"]),
        ("repo.read_file", {"filesystem": "read_only"}, []),
        ("code.write_file", {"filesystem": "read_only"}, ["fs_write"]),
        ("code.write_file", {"filesystem": "test_only"}, []),
        ("code.delete_file", {"filesystem": "docs_only"}, ["fs_scoped_write"]),
        ("code.delete_file", {"filesystem": "scoped_write"}, []),
        ("git.diff", {"git": "none"}, ["git_read"]),
        ("git.diff", {"git": "read"}, []),
        ("git.commit_task", {"git": "read"}, ["git_branch_commit"]),
        ("tests.run", {"sandbox": "deny"}, ["sandbox_allow"]),
        ("tests.run", {"sandbox": "allow"}, []),
        ("deps.install", {"sandbox": "deny", "network": "deny"}, ["network_allowlist", "sandbox_allow"]),
        ("repo.detect_stack", {"filesystem": "none", "git": "none", "sandbox": "deny"}, []),
    ],
)
def test_permission_matrix(tool: str, perms: dict[str, str], unmet: list[str]) -> None:
    from tests.tool_fakes import permissions

    assert unmet_preconditions(TOOL_CATALOG[tool], permissions(**perms), frozenset({tool})) == unmet


def test_an_unknown_precondition_is_unmet() -> None:
    from app.tools.catalog import ToolInfo
    from tests.tool_fakes import permissions

    odd = ToolInfo("x.y", "R0", "backend", ("telepathy",), "mvp")
    assert unmet_preconditions(odd, permissions(), frozenset()) == ["telepathy"]


@pytest.mark.parametrize(
    ("filesystem", "path", "allowed"),
    [
        ("test_only", "tests/test_a.py", True),
        ("test_only", "tests/deep/fixture.json", True),
        ("test_only", "test_a.py", True),
        ("test_only", "pkg/test_a.py", True),
        ("test_only", "pkg/a_test.py", True),
        ("test_only", "web/src/App.test.tsx", True),
        ("test_only", "web/src/App.spec.ts", True),
        ("test_only", "web/src/__tests__/x.js", True),
        ("test_only", "src/app.py", False),
        ("test_only", "pkg/test_a.pyc", False),
        ("test_only", "Tests/test_a.txt", False),
        ("test_only", "../tests/test_a.py", False),
        ("test_only", "/tests/test_a.py", False),
        ("test_only", "tests/../src/app.py", False),
        ("test_only", "tests/./x.py", False),
        ("test_only", "tests\\x.py", False),
        ("test_only", "", False),
        ("docs_only", "README.md", True),
        ("docs_only", "docs/guide.txt", True),
        ("docs_only", "a/b/notes.md", True),
        ("docs_only", "src/app.py", False),
        ("docs_only", "docs/../app.py", False),
        ("scoped_write", "src/app.py", True),
        ("scoped_write", "../outside.py", False),
        ("scoped_write", "a\x00b", False),
        ("read_only", "README.md", False),
        ("none", "README.md", False),
    ],
)
def test_write_path_rules(filesystem: str, path: str, allowed: bool) -> None:
    assert write_allowed(filesystem, path) is allowed


def test_r4_lists_are_one_source() -> None:
    assert is_r4("shell.exec") and is_r4("approval.decide") and is_r4("git.force_push")
    assert is_r4("anything.approve_all") and is_r4("env.read")
    assert not any(is_r4(name) for name in TOOL_CATALOG)


def test_finished_task_statuses_match_the_state_machine() -> None:
    from app.tools.gateway import FINISHED_TASK_STATUSES
    from app.workflow.states import FINISHED

    assert {status.value for status in FINISHED} == FINISHED_TASK_STATUSES
