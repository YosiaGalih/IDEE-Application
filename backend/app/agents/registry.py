"""Agent registry loader, schema 2 (FR-041 to FR-043, AGENT_CATALOG §2 to §4, §12.2).

The API refuses to start on a registry that breaks a rule here: structure and enums (no duplicate YAML key),
the mode, profile and contract rules, ``approval_grant`` always ``deny``, the per-agent pinned permissions,
every allowlisted tool known and consistent with the permissions, the ``submit_result`` rules and the limits.
Documentation fidelity (names and capabilities copied from the PRD, exactly 53 ids) stays in
``scripts/validate_registry.py`` in CI: a doc wording drift is not a reason to refuse to start.
test_registry_loader.py proves both refuse the same unsafe registries.

Problems name the agent id and the rule, never field contents.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictInt, ValidationError

from app.tools.catalog import CITATION_SOURCE_TOOLS, R3_OWNERS, TOOL_CATALOG
from app.tools.policy import SANDBOX, unmet_preconditions

SCHEMA_VERSION = 2
Group = Literal[
    "planning_intent",
    "software_development",
    "testing_debugging",
    "quality_security",
    "git_collaboration",
    "devops_operations",
    "documentation_knowledge",
    "specialized_engineering",
    "orchestration",
    "ai_assistant",
    "integrations",
    "portfolio",
]
Mode = Literal["llm", "profile", "tool", "planned"]
TargetMode = Literal["llm", "profile", "tool"]
Priority = Literal["P0", "P1", "P2"]
ModelTier = Literal["fast", "default", "complex"]

# The six runtimes and their I/O contracts (AGENT_CATALOG §7). The contract classes arrive with the
# AgentRuntime (WP-3.1f, app/agents/contracts.py); until then this is the map the CI validator falls back to.
RUNTIME_CONTRACTS: Mapping[str, tuple[str, str]] = {
    "A02": ("IntakeInput", "IntakeOutput"),
    "A03": ("PlanInput", "PlanOutput"),
    "B14": ("ImplementInput", "ImplementOutput"),
    "C20": ("DebugInput", "DebugOutput"),
    "D22": ("ReviewInput", "ReviewOutput"),
    "A07": ("UserProxyInput", "UserProxyOutput"),
}
ONLY_DEPLOYER = frozenset({"F32"})
# Permission fields pinned by the PRD access notes, AC-10 and FR-043 (the same table as the CI validator's):
# a reviewer or the user proxy that could write, commit or execute is refused at start, not only in CI.
PINNED_PERMISSIONS: Mapping[str, Mapping[str, str]] = {
    "A01": {"filesystem": "read_only", "git": "none"},
    "A02": {"filesystem": "read_only", "git": "none"},
    "A03": {"filesystem": "read_only", "git": "none"},
    "A04": {"filesystem": "read_only", "git": "none"},
    "A05": {"filesystem": "read_only", "git": "none"},
    "A06": {"filesystem": "read_only", "git": "read"},
    "A07": {"filesystem": "read_only", "sandbox": "deny", "deployment": "deny", "git": "read"},
    "C16": {"filesystem": "read_only", "git": "none"},
    "C17": {"filesystem": "test_only"},
    "D22": {"filesystem": "read_only", "sandbox": "deny", "git": "read"},
    "D23": {"filesystem": "read_only", "git": "none"},
    "D26": {"filesystem": "read_only", "git": "none"},
    "F33": {"filesystem": "read_only", "git": "none"},
    "G35": {"filesystem": "docs_only"},
    "G36": {"filesystem": "docs_only"},
    "B14": {"filesystem": "scoped_write", "deployment": "deny"},
    "E27": {"git": "branch_commit"},
    "E29": {"filesystem": "read_only", "git": "read"},
}
CITED_ANSWER = "CitedAnswer"
_V1_PERMISSION_FIELDS = frozenset({"filesystem", "sandbox", "git", "network", "deployment", "approval_grant"})
_V2_PERMISSION_FIELDS = _V1_PERMISSION_FIELDS | {"portfolio", "data_sensitivity_max"}

Text = Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
Name = Annotated[str, Field(min_length=1, max_length=120, pattern=r"\S")]
Snake = Annotated[str, Field(max_length=60, pattern=r"^[a-z][a-z0-9_]*$")]
Capability = Annotated[str, Field(max_length=120, pattern=r"^[a-z0-9_]+\.[a-z0-9_]+$")]
ToolName = Annotated[str, Field(max_length=64)]
ContractName = Annotated[str, Field(max_length=60, pattern=r"^[A-Z][A-Za-z0-9]*$")]
# ASCII digits only: ``\d`` would also take other scripts' digits.
AGENT_ID_PATTERN = r"^(?:[A-H][0-9]{2}|[IJKS][0-9]{2}|ORCH)$"


def _list_only(value: object) -> object:
    # A YAML !!set or a mapping would load in lax mode with no stable order, hence no stable hash.
    if not isinstance(value, list):
        raise ValueError("must be a list")
    return value


Listed = BeforeValidator(_list_only)


class RegistryError(Exception):
    """The registry cannot be used. ``problems`` are ``"<agent id>: <rule>"`` lines without field contents."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"agent registry invalid: {len(problems)} problem(s): " + "; ".join(problems[:20]))
        self.problems = problems


class PermissionProfile(BaseModel):
    """AGENT_CATALOG §3 + v2 (§12.2). Six-field (v1) entries get the v2 defaults, which fail closed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    filesystem: Literal["none", "read_only", "test_only", "docs_only", "scoped_write"]
    sandbox: Literal["deny", "allow"]
    git: Literal["none", "read", "branch_commit"]
    network: Literal["deny", "allowlist", "provider_web"]
    deployment: Literal["deny", "staging_with_approval"]
    portfolio: Literal["none", "draft"] = "none"
    data_sensitivity_max: Literal["public", "internal", "confidential"] = "confidential"
    approval_grant: Literal["deny"]  # no agent can approve anything (PRD §10.4, FR-043)


class Limits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_iterations: Annotated[StrictInt, Field(ge=1, le=40)]
    timeout_seconds: Annotated[StrictInt, Field(ge=30, le=3600)]
    tool_quota: dict[ToolName, Annotated[StrictInt, Field(ge=1, le=100)]] | None = None


class AgentSpec(BaseModel):
    """One registry entry, validated (registry schema 2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: Annotated[str, Field(pattern=AGENT_ID_PATTERN)]
    slug: Snake
    name: Name
    group: Group
    version: Annotated[StrictInt, Field(ge=1)]
    priority: Priority
    mvp_mode: Mode
    runtime_agent: str | None
    enabled: StrictBool
    model_tier: ModelTier
    description: Text
    purpose: Text
    triggers: Annotated[tuple[Snake, ...], Listed, Field(min_length=1)]
    capabilities: Annotated[tuple[Capability, ...], Listed, Field(min_length=1)]
    tools: Annotated[tuple[ToolName, ...], Listed]
    permissions: PermissionProfile
    input_contract: ContractName | None
    output_contract: ContractName | None
    limits: Limits
    inputs_tools_note: Text
    output_note: Text
    access_note: Text
    target_mode: TargetMode | None = None
    phase: Annotated[str, Field(pattern=r"^P[0-9]{1,2}$")] | None = None
    contract_status: Literal["planned"] | None = None
    notes: Annotated[tuple[Text, ...], Listed, Field(min_length=1)] | None = None

    def canonical_hash(self) -> str:
        """sha256 of the entry as canonical JSON: changes exactly when the entry's content changes."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def runtime_kind(self) -> str | None:
        """``mvp_mode`` for active agents, ``target_mode`` for planned ones."""
        return self.target_mode if self.mvp_mode == "planned" else self.mvp_mode


class AgentRegistry(BaseModel):
    model_config = ConfigDict(frozen=True)

    agents: tuple[AgentSpec, ...]

    def by_id(self, agent_id: str) -> AgentSpec | None:
        return next((agent for agent in self.agents if agent.agent_id == agent_id), None)


class _DuplicateKey(yaml.YAMLError):
    def __init__(self, line: int) -> None:
        super().__init__(f"duplicate key at line {line}")
        self.line = line


class _UniqueKeyLoader(yaml.SafeLoader):
    """``safe_load`` that refuses a key repeated in one mapping instead of keeping the last one."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: list[object] = []
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise _DuplicateKey(key_node.start_mark.line + 1)
        seen.append(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def load_registry(path: Path) -> AgentRegistry:
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)  # noqa: S506 - a SafeLoader
    except _DuplicateKey as exc:
        raise RegistryError([f"registry: duplicate key at line {exc.line}"]) from None
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryError([f"registry: cannot read {path.name} ({type(exc).__name__})"]) from None
    return parse_registry(raw)


def parse_registry(raw: object) -> AgentRegistry:
    if not isinstance(raw, dict):
        raise RegistryError(["registry: root must be a mapping with schema_version and agents"])
    problems: list[str] = []
    if set(raw) != {"schema_version", "agents"}:
        problems.append("registry: top-level keys must be exactly schema_version and agents")
    if raw.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"registry: schema_version must be {SCHEMA_VERSION}")
    entries = raw.get("agents")
    if not isinstance(entries, list) or not entries:
        raise RegistryError([*problems, "registry: agents must be a non-empty list"])
    agents: list[AgentSpec] = []
    for index, entry in enumerate(entries):
        label = _label(entry, index)
        try:
            agents.append(AgentSpec.model_validate(entry))
        except ValidationError as exc:
            problems.extend(f"{label}: {_where(error['loc'])} {error['msg']}" for error in exc.errors())
            continue
        problems.extend(f"{label}: {rule}" for rule in _permission_shape(entry))
    problems.extend(_identity_problems(agents))
    by_id = {agent.agent_id: agent for agent in agents}
    for agent in agents:
        problems.extend(f"{agent.agent_id}: {rule}" for rule in _agent_problems(agent, by_id))
    if problems:
        raise RegistryError(problems)
    return AgentRegistry(agents=tuple(agents))


def _label(entry: object, index: int) -> str:
    agent_id = entry.get("agent_id") if isinstance(entry, dict) else None
    # Only an id that looks like one is echoed; anything else is named by its position.
    if isinstance(agent_id, str) and len(agent_id) <= 4 and agent_id.isascii() and agent_id.isalnum():
        return agent_id
    return f"agents[{index}]"


_KEY = re.compile(r"[a-z_][a-z0-9_]{0,39}")


def _where(loc: Iterable[int | str]) -> str:
    # A key is file content too: only short identifier-like keys are named, never a sentence or a secret.
    return ".".join(str(part) if isinstance(part, int) or _KEY.fullmatch(part) else "<key>" for part in loc)


def _permission_shape(entry: Mapping[str, Any]) -> list[str]:
    fields = set(entry.get("permissions") or {})
    if entry.get("target_mode") is not None and fields != _V2_PERMISSION_FIELDS:
        return ["permissions: v2 entries declare all eight fields"]
    if fields not in (_V1_PERMISSION_FIELDS, _V2_PERMISSION_FIELDS):
        return ["permissions: must have the six v1 fields or the eight v2 fields"]
    return []


def _identity_problems(agents: list[AgentSpec]) -> list[str]:
    problems = [
        f"{aid}: agent_id is not unique" for aid, n in Counter(a.agent_id for a in agents).items() if n > 1
    ]
    problems += [
        f"{a.agent_id}: slug is not unique" for a in agents if [b.slug for b in agents].count(a.slug) > 1
    ]
    llm = {a.agent_id for a in agents if a.mvp_mode == "llm"}
    if llm != set(RUNTIME_CONTRACTS):
        problems.append("registry: the llm agents must be exactly the six runtimes with contracts")
    return problems


def _agent_problems(agent: AgentSpec, by_id: Mapping[str, AgentSpec]) -> list[str]:
    return [
        *_mode_problems(agent, by_id),
        *_permission_problems(agent),
        *_tool_problems(agent),
        *_submit_problems(agent),
        *_limit_problems(agent),
    ]


def _mode_problems(agent: AgentSpec, by_id: Mapping[str, AgentSpec]) -> list[str]:
    problems: list[str] = []
    mode, target = agent.mvp_mode, agent.target_mode
    if agent.enabled != (mode != "planned"):
        problems.append("enabled must be true exactly when mvp_mode is not planned")
    if target is not None and mode != "planned":
        problems.append("target_mode is only for planned agents")
    if (target is None) != (agent.phase is None):
        problems.append("target_mode and phase go together")
    runtime = by_id.get(agent.runtime_agent) if agent.runtime_agent else None
    pair = (agent.input_contract, agent.output_contract)
    if mode == "profile":
        if runtime is None or runtime.mvp_mode != "llm":
            problems.append("runtime_agent must name an llm agent")
        elif pair != RUNTIME_CONTRACTS.get(runtime.agent_id):
            problems.append("profile contracts must equal its runtime agent's")
    elif agent.runtime_agent is not None:
        problems.append("runtime_agent is only for profile agents")
    if mode == "llm" and pair != RUNTIME_CONTRACTS.get(agent.agent_id):
        problems.append("llm contracts must equal the runtime contract map")
    if mode == "tool" and pair != (None, None):
        problems.append("tool agents have no contracts")
    if mode == "planned" and target is None and pair != (None, None):
        problems.append("planned agents without target_mode have no contracts")
    if mode == "planned" and target is not None and (None in pair or agent.contract_status != "planned"):
        problems.append("planned agents with target_mode declare contracts with contract_status planned")
    if agent.contract_status is not None and (mode != "planned" or target is None):
        problems.append("contract_status is only for planned agents with target_mode")
    return problems


def _permission_problems(agent: AgentSpec) -> list[str]:
    perms = agent.permissions
    problems: list[str] = []
    for field, value in PINNED_PERMISSIONS.get(agent.agent_id, {}).items():
        if getattr(perms, field) != value:
            problems.append(f"permissions.{field} must be {value} (pinned by the PRD access note)")
    if perms.deployment != "deny" and agent.agent_id not in ONLY_DEPLOYER:
        problems.append("deployment is reserved for F32")
    if perms.network == "provider_web" and not any(
        TOOL_CATALOG[t].executor == "provider_anthropic" for t in agent.tools if t in TOOL_CATALOG
    ):
        problems.append("network provider_web needs a provider server tool (least privilege)")
    if perms.portfolio == "draft" and "portfolio.draft_item" not in agent.tools:
        problems.append("portfolio draft needs portfolio.draft_item (least privilege)")
    return problems


def _tool_problems(agent: AgentSpec) -> list[str]:
    problems: list[str] = []
    if len(set(agent.tools)) != len(agent.tools):
        problems.append("tools has duplicates")
    for name in agent.tools:
        tool = TOOL_CATALOG.get(name)
        if tool is None:
            problems.append(f"tool {_tool_label(name)} is not in the tool catalog")
            continue
        if name == "submit_result":
            continue
        problems += [f"tool {name} {rule}" for rule in _precondition_problems(name, agent)]
        if agent.enabled and tool.status != "mvp":
            problems.append(f"tool {name} is {tool.status} and must not be on an enabled agent")
        if tool.risk == "R3":
            owners = R3_OWNERS.get(name)
            if owners is not None and agent.agent_id not in owners:
                problems.append(f"tool {name} (R3) is reserved for other agents")
            if agent.runtime_kind in {"llm", "profile"}:
                problems.append(f"tool {name} (R3) is never allowed for llm/profile agents")
    return problems


def _tool_label(name: str) -> str:
    return name if len(name) <= 64 and name.replace(".", "").replace("_", "").isalnum() else "<invalid name>"


def _precondition_problems(name: str, agent: AgentSpec) -> list[str]:
    # The same rules the ToolGateway enforces at run time (app.tools.policy).
    unmet = unmet_preconditions(TOOL_CATALOG[name], agent.permissions, frozenset(agent.tools))
    return [
        "requires sandbox allow (R2)"
        if pre == SANDBOX
        else f"requires {pre} (permissions inconsistent with allowlist)"
        for pre in unmet
    ]


def _submit_problems(agent: AgentSpec) -> list[str]:
    kind, tools = agent.runtime_kind, agent.tools
    has_submit = "submit_result" in tools
    if agent.output_contract == CITED_ANSWER:
        problems = ["CitedAnswer agents end with cited text, not submit_result"] if has_submit else []
        if not any(t in CITATION_SOURCE_TOOLS for t in tools):
            problems.append("CitedAnswer agents need a citation-source tool")
        return problems
    if kind in {"llm", "profile"}:
        problems = [] if has_submit else ["llm/profile agents must allowlist submit_result"]
        if not [t for t in tools if t != "submit_result"]:
            problems.append("llm/profile agents need a tool besides submit_result")
        return problems
    return ["submit_result is only for llm/profile agents"] if has_submit else []


def _limit_problems(agent: AgentSpec) -> list[str]:
    limits, problems = agent.limits, []
    if limits.tool_quota is not None:
        if agent.target_mode is None:
            problems.append("limits.tool_quota is only for v2 entries")
        if not limits.tool_quota or not set(limits.tool_quota) <= set(agent.tools):
            problems.append("limits.tool_quota keys must be tools of the agent")
    if agent.target_mode == "tool" and limits.max_iterations != 1:
        problems.append("target_mode tool means max_iterations 1")
    if agent.runtime_kind in {"llm", "profile"} and not 300 <= limits.timeout_seconds <= 900:
        problems.append("llm/profile timeout_seconds must be within 300..900")
    return problems
