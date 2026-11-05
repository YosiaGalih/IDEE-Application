"""The registry loader (TEST_STRATEGY §2.1): the real registry loads; every registry that breaks a security
rule is refused, by the loader and by the CI validator alike (parity, spec WP-3.1b goal 2)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml

from app.agents.registry import PINNED_PERMISSIONS, Group, Mode, RegistryError, load_registry, parse_registry
from app.models.enums import AGENT_GROUPS, AGENT_MODES
from tests.unit.test_tool_catalog import ci_validator

REGISTRY = Path(__file__).resolve().parents[3] / "agents" / "registry.v2.yaml"
Raw = dict[str, Any]


@pytest.fixture(scope="module")
def raw() -> Raw:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def agent(data: Raw, agent_id: str) -> Raw:
    return next(entry for entry in data["agents"] if entry["agent_id"] == agent_id)


def first_planned(data: Raw) -> Raw:
    return next(entry for entry in data["agents"] if entry["mvp_mode"] == "planned")


def test_repo_registry_loads() -> None:
    registry = load_registry(REGISTRY)
    assert len(registry.agents) == 53
    assert sum(1 for a in registry.agents if a.enabled) == 20
    a02 = registry.by_id("A02")
    assert a02 is not None
    assert a02.permissions.approval_grant == "deny"
    assert a02.permissions.data_sensitivity_max == "confidential"  # v2 default for a 6-field entry
    assert registry.by_id("Z99") is None


def _set(path: list[str], value: Any, agent_id: str) -> Callable[[Raw], None]:
    def mutate(data: Raw) -> None:
        target = agent(data, agent_id)
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    return mutate


def _add_tool(agent_id: str, tool: str) -> Callable[[Raw], None]:
    return lambda data: agent(data, agent_id)["tools"].append(tool)


def _drop_tool(agent_id: str, tool: str) -> Callable[[Raw], None]:
    return lambda data: agent(data, agent_id)["tools"].remove(tool)


def _planned_enabled(data: Raw) -> None:
    first_planned(data)["enabled"] = True


def _duplicate_a02(data: Raw) -> None:
    data["agents"].append(copy.deepcopy(agent(data, "A02")))


def _tools_as_set(data: Raw) -> None:
    # A YAML !!set: no order, so no stable hash (AG-13 I3).
    agent(data, "A04")["tools"] = set(agent(data, "A04")["tools"])


def _schema_1(data: Raw) -> None:
    data["schema_version"] = 1


# name -> (mutation, a fragment of the loader's problem that names the broken rule)
MUTATIONS: dict[str, tuple[Callable[[Raw], None], str]] = {
    "approval_grant allow": (
        _set(["permissions", "approval_grant"], "allow", "A02"),
        "A02: permissions.approval_grant",
    ),
    "write tool on a read-only reviewer": (
        _add_tool("D22", "code.write_file"),
        "D22: tool code.write_file requires fs_write",
    ),
    "R2 tool without sandbox": (_add_tool("A02", "tests.run"), "A02: tool tests.run requires sandbox allow"),
    "disabled tool on an enabled agent": (
        _add_tool("B14", "deps.install"),
        "B14: tool deps.install is disabled",
    ),
    "R3 tool on an llm agent": (_add_tool("B14", "git.push"), "B14: tool git.push (R3) is never allowed"),
    "tool outside the catalog": (
        _add_tool("A02", "shell.exec"),
        "A02: tool shell.exec is not in the tool catalog",
    ),
    "deployment outside F32": (
        _set(["permissions", "deployment"], "staging_with_approval", "B14"),
        "B14: deployment is reserved for F32",
    ),
    "provider_web without a server tool": (
        _set(["permissions", "network"], "provider_web", "A02"),
        "A02: network provider_web needs a provider server tool",
    ),
    "profile on a tool agent": (
        _set(["runtime_agent"], "A04", "A01"),
        "A01: runtime_agent must name an llm agent",
    ),
    "profile contract differs": (
        _set(["output_contract"], "PlanOutput", "A01"),
        "A01: profile contracts must equal",
    ),
    "llm contract differs": (
        _set(["output_contract"], "ReviewOutput", "A02"),
        "A02: llm contracts must equal",
    ),
    "llm agent without submit_result": (
        _drop_tool("A02", "submit_result"),
        "A02: llm/profile agents must allowlist submit_result",
    ),
    "tool agent with submit_result": (
        _add_tool("A04", "submit_result"),
        "A04: submit_result is only for llm/profile",
    ),
    "planned agent enabled": (_planned_enabled, "enabled must be true exactly when"),
    "max_iterations over 40": (_set(["limits", "max_iterations"], 41, "A02"), "A02: limits.max_iterations"),
    "llm timeout over 900": (
        _set(["limits", "timeout_seconds"], 1000, "A02"),
        "A02: llm/profile timeout_seconds",
    ),
    "duplicate agent id": (_duplicate_a02, "A02: agent_id is not unique"),
    # Pinned permissions (PRD access notes, AC-10, FR-043): read-only agents stay read-only (AG-13 M1).
    "reviewer given scoped_write": (
        _set(["permissions", "filesystem"], "scoped_write", "D22"),
        "D22: permissions.filesystem must be read_only",
    ),
    "reviewer given the sandbox": (
        _set(["permissions", "sandbox"], "allow", "D22"),
        "D22: permissions.sandbox must be deny",
    ),
    "user proxy given scoped_write": (
        _set(["permissions", "filesystem"], "scoped_write", "A07"),
        "A07: permissions.filesystem must be read_only",
    ),
    "test writer given scoped_write": (
        _set(["permissions", "filesystem"], "scoped_write", "C17"),
        "C17: permissions.filesystem must be test_only",
    ),
    "docs writer given scoped_write": (
        _set(["permissions", "filesystem"], "scoped_write", "G35"),
        "G35: permissions.filesystem must be docs_only",
    ),
    "user proxy given commit": (
        _set(["permissions", "git"], "branch_commit", "A07"),
        "A07: permissions.git must be read",
    ),
    "git operator given no git": (
        _set(["permissions", "git"], "read", "E27"),
        "E27: permissions.git must be branch_commit",
    ),
    "tools given as a set": (_tools_as_set, "A04: tools"),
    "phase with a non-ASCII digit": (_set(["phase"], "P\u0669", "E29"), "E29: phase"),
    "agent id with a non-ASCII digit": (_set(["agent_id"], "A\u0669\u0669", "A02"), "agents["),
    "unknown field": (_set(["shell"], "bash", "A02"), "A02: shell Extra inputs are not permitted"),
    "schema version 1": (_schema_1, "registry: schema_version must be 2"),
}


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_each_mutation_is_refused_by_loader_and_ci_validator(raw: Raw, name: str) -> None:
    mutate, expected = MUTATIONS[name]
    data = copy.deepcopy(raw)
    mutate(data)
    assert ci_validator().validate(data), f"CI validator accepted: {name}"
    with pytest.raises(RegistryError) as raised:
        parse_registry(data)
    assert any(expected in problem for problem in raised.value.problems), raised.value.problems


def test_the_real_registry_passes_both(raw: Raw) -> None:
    assert ci_validator().validate(copy.deepcopy(raw)) == []
    parse_registry(copy.deepcopy(raw))


def test_problems_name_the_agent_and_rule_only(raw: Raw) -> None:
    data = copy.deepcopy(raw)
    a02 = agent(data, "A02")
    a02["permissions"]["approval_grant"] = "allow"
    a02["description"] = "SECRET-DESCRIPTION-TEXT"
    with pytest.raises(RegistryError) as raised:
        parse_registry(data)
    assert any(
        problem.startswith("A02: ") and "approval_grant" in problem for problem in raised.value.problems
    )
    assert all("SECRET-DESCRIPTION-TEXT" not in problem for problem in raised.value.problems)
    assert "SECRET-DESCRIPTION-TEXT" not in str(raised.value)


def test_odd_key_names_are_not_echoed(raw: Raw) -> None:
    # A key is file content too: only short identifier-like keys are named (spec goal 2, code review).
    data = copy.deepcopy(raw)
    agent(data, "A02")["permissions"]["sk-ant-api03-LEAKED-KEY"] = "deny"
    agent(data, "A04")["limits"]["tool_quota"] = {"quota for a SECRET sentence": 0}
    with pytest.raises(RegistryError) as raised:
        parse_registry(data)
    text = "; ".join(raised.value.problems)
    assert "LEAKED" not in text and "SECRET" not in text
    assert "A02: permissions.<key> Extra inputs are not permitted" in raised.value.problems
    assert any(p.startswith("A04: limits.tool_quota.<key>") for p in raised.value.problems)


@pytest.mark.parametrize(
    "bad", [None, [], "agents", {"schema_version": 2}, {"schema_version": 2, "agents": {}}]
)
def test_malformed_roots_are_refused(bad: object) -> None:
    with pytest.raises(RegistryError):
        parse_registry(bad)


def test_missing_or_broken_file(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("agents: [unclosed", encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(broken)


def test_duplicate_keys_are_refused(tmp_path: Path) -> None:
    # The last duplicate would win silently, and a review would read the first one (silent M1, AG-13 I1).
    text = REGISTRY.read_text(encoding="utf-8").replace(
        "approval_grant: deny", "approval_grant: deny, approval_grant: deny", 1
    )
    assert text != REGISTRY.read_text(encoding="utf-8")
    twice = tmp_path / "registry.yaml"
    twice.write_text(text, encoding="utf-8")
    with pytest.raises(RegistryError) as raised:
        load_registry(twice)
    assert raised.value.problems == [
        f"registry: duplicate key at line {_line_of(text, 'approval_grant: deny, approval_grant')}"
    ]
    assert ci_validator().main([str(twice)]) != 0


def _line_of(text: str, needle: str) -> int:
    return text[: text.index(needle)].count("\n") + 1


def test_pinned_permissions_match_the_ci_validator() -> None:
    assert dict(PINNED_PERMISSIONS) == ci_validator().PINNED_PERMISSIONS


def test_enums_match_the_tables() -> None:
    assert get_args(Group) == AGENT_GROUPS
    assert get_args(Mode) == AGENT_MODES


def test_canonical_hash_changes_with_content(raw: Raw) -> None:
    before = parse_registry(copy.deepcopy(raw)).by_id("A02")
    data = copy.deepcopy(raw)
    agent(data, "A02")["description"] = "changed"
    after = parse_registry(data).by_id("A02")
    assert before is not None and after is not None
    assert before.canonical_hash() != after.canonical_hash()
    assert before.canonical_hash() == parse_registry(copy.deepcopy(raw)).by_id("A02").canonical_hash()  # type: ignore[union-attr]
