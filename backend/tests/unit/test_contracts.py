"""The six runtime I/O contracts (spec WP-3.1f Keputusan Lead #25; AGENT_CATALOG §7; ADR-013).

Every class forbids extra fields and is bounded; keys and paths follow their patterns; the maps are dict
literals so the CI validator reads them through ``ast``; no field claims a test status or an approval.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Iterator
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from app.agents import contracts as c
from app.agents.registry import RUNTIME_CONTRACTS
from app.models.enums import WORKFLOW_TYPES
from tests.unit.test_tool_catalog import ci_validator

UUID1 = "123e4567-e89b-42d3-a456-426614174000"
UUID2 = "00000000-0000-4000-8000-000000000002"

CRITERION: dict[str, Any] = {
    "key": "AC-1",
    "criterion": "c",
    "verification_method": "test",
    "mandatory": True,
}
REQUIREMENT: dict[str, Any] = {
    "key": "REQ-1",
    "title": "t",
    "description": "d",
    "priority": "must",
    "acceptance_criteria": [CRITERION],
}
STEP: dict[str, Any] = {
    "id": "P1",
    "title": "t",
    "specialist": "B14",
    "description": "d",
    "depends_on": [],
    "requirement_keys": ["REQ-1"],
    "files_hint": ["src/app.py"],
}
FINDING: dict[str, Any] = {
    "severity": "low",
    "category": "style",
    "title": "t",
    "file_path": "src/app.py",
    "line_start": 1,
    "line_end": 2,
    "evidence": "e",
    "recommendation": "r",
}
CRITERION_VIEW: dict[str, Any] = {
    "key": "AC-1",
    "requirement_key": "REQ-1",
    "criterion": "c",
    "verification_method": "test",
    "mandatory": True,
}
CRITERION_RESULT: dict[str, Any] = {
    "criterion_key": "AC-1",
    "status": "unverified",
    "evidence_refs": [f"test_run:{UUID1}"],
    "rationale": "r",
}

VALID: dict[type[BaseModel], dict[str, Any]] = {
    c.IntakeInput: {"task_id": UUID1, "prompt": "Add a calculator"},
    c.IntakeOutput: {
        "title": "t",
        "summary": "s",
        "workflow_type": "create",
        "complexity": "low",
        "risk": "low",
        "requirements": [REQUIREMENT],
        "constraints": [],
        "detected_stack_hints": [],
        "clarification_questions": [],
    },
    c.PlanInput: {"task_id": UUID1, "workflow_type": "create", "requirements": [REQUIREMENT]},
    c.PlanOutput: {"steps": [STEP], "test_plan": {"summary": "s", "criterion_keys": ["AC-1"]}, "risks": []},
    c.ImplementInput: {"task_id": UUID1, "step": STEP, "requirements": [REQUIREMENT]},
    c.ImplementOutput: {"summary": "s", "changed_files": [], "evidence_refs": [], "follow_ups": []},
    c.DebugInput: {
        "task_id": UUID1,
        "failure": {"category": "assertion", "summary": "s"},
        "attempt": 1,
        "requirements": [],
    },
    c.DebugOutput: {
        "root_cause": "r",
        "category": "assertion",
        "fix_summary": "f",
        "changed_files": [],
        "next_step": "retest",
        "evidence_refs": [],
    },
    c.ReviewInput: {"task_id": UUID1, "diff_ref": f"diff:{UUID1}", "changed_files": [], "requirements": []},
    c.ReviewOutput: {"summary": "s", "findings": [FINDING]},
    c.UserProxyInput: {"task_id": UUID1, "baseline_version": 1, "criteria": [CRITERION_VIEW], "evidence": []},
    c.UserProxyOutput: {
        "status": "unverified",
        "summary": "s",
        "criteria_results": [CRITERION_RESULT],
        "next_actions": [],
        "confidence": "low",
    },
}
CONTRACTS = list(VALID)


def with_(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**base, **changes}


def error_types(exc: ValidationError) -> set[str]:
    return {str(error["type"]) for error in exc.errors()}


def test_every_contract_has_a_valid_example() -> None:
    assert {cls.__name__ for cls in CONTRACTS} == {
        name for pair in RUNTIME_CONTRACTS.values() for name in pair
    }
    for cls, example in VALID.items():
        cls.model_validate(example)


@pytest.mark.parametrize("cls", CONTRACTS, ids=lambda cls: cls.__name__)
def test_every_contract_forbids_extra_fields(cls: type[BaseModel]) -> None:
    with pytest.raises(ValidationError) as exc:
        cls.model_validate(with_(VALID[cls], tests_status="passed"))
    assert "extra_forbidden" in error_types(exc.value)


def _objects(schema: Any) -> Iterator[dict[str, Any]]:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for value in schema.values():
            yield from _objects(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _objects(value)


@pytest.mark.parametrize("cls", CONTRACTS, ids=lambda cls: cls.__name__)
def test_every_schema_forbids_additional_properties(cls: type[BaseModel]) -> None:
    objects = list(_objects(cls.model_json_schema()))
    assert objects
    assert all(obj.get("additionalProperties") is False for obj in objects)


def test_contract_maps_follow_the_registry_runtimes() -> None:
    assert set(c.OUTPUT_CONTRACTS) == set(c.INPUT_CONTRACTS) == set(RUNTIME_CONTRACTS)
    for agent_id, pair in RUNTIME_CONTRACTS.items():
        assert (c.INPUT_CONTRACTS[agent_id].__name__, c.OUTPUT_CONTRACTS[agent_id].__name__) == pair


def test_ci_validator_reads_the_contract_maps() -> None:
    # Proves the maps are module-level dict literals (scripts/validate_registry.py parses them with ast).
    assert ci_validator().load_contracts() == dict(RUNTIME_CONTRACTS)


KEY_CASES: list[tuple[type[BaseModel], dict[str, Any], bool]] = [
    (c.Requirement, with_(REQUIREMENT, key="REQ-0"), False),
    (c.Requirement, with_(REQUIREMENT, key="REQ-1000"), False),
    (c.Requirement, with_(REQUIREMENT, key="REQ-01"), False),
    (c.Requirement, with_(REQUIREMENT, key="REQ-999"), True),
    (c.Criterion, with_(CRITERION, key="AC-x"), False),
    (c.Criterion, with_(CRITERION, key="AC-0"), False),
    (c.Criterion, with_(CRITERION, key="AC-42"), True),
    (c.PlanStep, with_(STEP, id="P0"), False),
    (c.PlanStep, with_(STEP, id="P13"), False),
    (c.PlanStep, with_(STEP, id="P12"), True),
    (c.PlanStep, with_(STEP, id="P9"), True),
    (c.EvidenceItem, {"ref": "test_run:abc", "label": "l"}, False),
    (c.EvidenceItem, {"ref": f"test_run:{UUID1.upper()}", "label": "l"}, False),
    (c.EvidenceItem, {"ref": f"commit:{UUID1}", "label": "l"}, False),
    (c.EvidenceItem, {"ref": f"test_run:{UUID1}", "label": "l"}, True),
    (c.EvidenceItem, {"ref": f"artifact:{UUID1}", "label": "l"}, True),
    (c.EvidenceItem, {"ref": f"finding:{UUID1}", "label": "l"}, True),
    (c.EvidenceItem, {"ref": f"diff:{UUID1}", "label": "l"}, True),
    (c.ReviewInput, with_(VALID[c.ReviewInput], diff_ref=f"diff:{UUID1.upper()}"), False),
    (c.ReviewInput, with_(VALID[c.ReviewInput], diff_ref=f"test_run:{UUID1}"), False),
    (c.ImplementOutput, with_(VALID[c.ImplementOutput], commit="a" * 39), False),
    (c.ImplementOutput, with_(VALID[c.ImplementOutput], commit="A" * 40), False),
    (c.ImplementOutput, with_(VALID[c.ImplementOutput], commit="a" * 40), True),
    (c.Finding, with_(FINDING, category="Style"), False),
    (c.Finding, with_(FINDING, category="a" * 61), False),
    (c.Finding, with_(FINDING, category="a" * 60), True),
]


@pytest.mark.parametrize(("cls", "data", "valid"), KEY_CASES)
def test_keys_follow_their_patterns(cls: type[BaseModel], data: dict[str, Any], valid: bool) -> None:
    if valid:
        cls.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            cls.model_validate(data)


@pytest.mark.parametrize(
    ("path", "valid"),
    [
        ("/etc/passwd", False),
        ("../x", False),
        ("a/../../b", False),
        ("a/..", False),
        ("..", False),
        ("a\\b", False),
        ("a\x00b", False),
        ("src\n", False),
        ("", False),
        ("src/app.py", True),
        ("./docs/a.md", True),
        ("..foo/x", True),
    ],
)
def test_paths_are_relative(path: str, valid: bool) -> None:
    data = {"path": path, "change": "modified"}
    if valid:
        assert c.ChangedFile.model_validate(data).path == path
    else:
        with pytest.raises(ValidationError):
            c.ChangedFile.model_validate(data)


def test_a_dotdot_refusal_does_not_echo_the_path() -> None:
    with pytest.raises(ValidationError) as exc:
        c.ChangedFile.model_validate({"path": "a/../secret-name", "change": "added"})
    assert all("secret-name" not in str(error["msg"]) for error in exc.value.errors())


def test_contract_patterns_compile_with_the_default_engine() -> None:
    # A look-around pattern fails at class creation under Pydantic's Rust regex engine; a fresh process proves
    # the module imports with the default engine.
    done = subprocess.run(
        [sys.executable, "-c", "import app.agents.contracts"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


def _bounded(
    cls: type[BaseModel], base: dict[str, Any], field: str, limit: int, make: Callable[[int], Any]
) -> tuple[type[BaseModel], dict[str, Any], dict[str, Any]]:
    return cls, with_(base, **{field: make(limit)}), with_(base, **{field: make(limit + 1)})


def _text(n: int) -> str:
    return "x" * n


def _path(n: int) -> str:
    return "a/" + "b" * (n - 2)


def _requirements(n: int) -> list[dict[str, Any]]:
    return [
        with_(REQUIREMENT, key=f"REQ-{i + 1}", acceptance_criteria=[with_(CRITERION, key=f"AC-{i + 1}")])
        for i in range(n)
    ]


def _criteria(n: int) -> list[dict[str, Any]]:
    return [with_(CRITERION, key=f"AC-{i + 1}") for i in range(n)]


def _steps(n: int) -> list[dict[str, Any]]:
    return [with_(STEP, id=f"P{i + 1}") if i < 12 else with_(STEP, id="P12") for i in range(n)]


def _results(n: int) -> list[dict[str, Any]]:
    return [with_(CRITERION_RESULT, criterion_key=f"AC-{i + 1}") for i in range(n)]


def _refs(n: int) -> list[str]:
    return [f"test_run:{UUID1}"] * n


def _changed(n: int) -> list[dict[str, Any]]:
    return [{"path": f"f{i}.py", "change": "added"} for i in range(n)]


BOUNDS = [
    _bounded(c.IntakeInput, VALID[c.IntakeInput], "prompt", 20_000, _text),
    _bounded(c.IntakeOutput, VALID[c.IntakeOutput], "title", 200, _text),
    _bounded(c.IntakeOutput, VALID[c.IntakeOutput], "summary", 4000, _text),
    _bounded(c.IntakeOutput, VALID[c.IntakeOutput], "requirements", 20, _requirements),
    _bounded(c.IntakeOutput, VALID[c.IntakeOutput], "constraints", 20, lambda n: ["x"] * n),
    _bounded(c.IntakeOutput, VALID[c.IntakeOutput], "detected_stack_hints", 10, lambda n: ["x"] * n),
    _bounded(
        c.IntakeOutput,
        VALID[c.IntakeOutput],
        "clarification_questions",
        5,
        lambda n: [{"question": "q", "blocking": False}] * n,
    ),
    _bounded(c.Requirement, REQUIREMENT, "title", 300, _text),
    _bounded(c.Requirement, REQUIREMENT, "acceptance_criteria", 10, _criteria),
    _bounded(c.PlanOutput, VALID[c.PlanOutput], "steps", 12, _steps),
    _bounded(c.PlanOutput, VALID[c.PlanOutput], "risks", 10, lambda n: ["x"] * n),
    _bounded(c.PlanStep, STEP, "depends_on", 11, lambda n: ["P1"] * n),
    _bounded(c.PlanStep, STEP, "requirement_keys", 20, lambda n: ["REQ-1"] * n),
    _bounded(c.PlanStep, STEP, "files_hint", 20, lambda n: ["a.py"] * n),
    _bounded(
        c.TestPlan, {"summary": "s", "criterion_keys": []}, "criterion_keys", 100, lambda n: ["AC-1"] * n
    ),
    _bounded(c.ChangedFile, {"path": "a", "change": "added"}, "path", 1000, _path),
    _bounded(c.ImplementOutput, VALID[c.ImplementOutput], "changed_files", 200, _changed),
    _bounded(c.ImplementOutput, VALID[c.ImplementOutput], "evidence_refs", 20, _refs),
    _bounded(c.ImplementOutput, VALID[c.ImplementOutput], "follow_ups", 10, lambda n: ["x"] * n),
    _bounded(c.ImplementInput, VALID[c.ImplementInput], "requirements", 20, _requirements),
    _bounded(c.DebugInput, VALID[c.DebugInput], "attempt", 20, lambda n: n),
    _bounded(c.DebugOutput, VALID[c.DebugOutput], "changed_files", 200, _changed),
    _bounded(c.ReviewInput, VALID[c.ReviewInput], "changed_files", 200, lambda n: ["a.py"] * n),
    _bounded(c.ReviewOutput, VALID[c.ReviewOutput], "findings", 50, lambda n: [FINDING] * n),
    _bounded(c.UserProxyInput, VALID[c.UserProxyInput], "criteria", 100, lambda n: [CRITERION_VIEW] * n),
    _bounded(
        c.UserProxyInput,
        VALID[c.UserProxyInput],
        "evidence",
        200,
        lambda n: [{"ref": f"artifact:{UUID1}", "label": "l"}] * n,
    ),
    _bounded(c.UserProxyOutput, VALID[c.UserProxyOutput], "criteria_results", 100, _results),
    _bounded(c.UserProxyOutput, VALID[c.UserProxyOutput], "next_actions", 10, lambda n: ["x"] * n),
    _bounded(c.CriterionResult, CRITERION_RESULT, "evidence_refs", 20, _refs),
    _bounded(c.Finding, FINDING, "evidence", 4000, _text),
]


@pytest.mark.parametrize(("cls", "at_limit", "past_limit"), BOUNDS)
def test_strings_and_lists_are_bounded(
    cls: type[BaseModel], at_limit: dict[str, Any], past_limit: dict[str, Any]
) -> None:
    cls.model_validate(at_limit)
    with pytest.raises(ValidationError):
        cls.model_validate(past_limit)


@pytest.mark.parametrize(
    ("data", "field"),
    [
        (with_(VALID[c.IntakeOutput], requirements=[]), "requirements"),
        (with_(REQUIREMENT, acceptance_criteria=[]), "acceptance_criteria"),
        (with_(VALID[c.PlanOutput], steps=[]), "steps"),
        (with_(VALID[c.DebugInput], attempt=0), "attempt"),
        (with_(VALID[c.UserProxyInput], baseline_version=0), "baseline_version"),
        (with_(CRITERION, criterion=""), "criterion"),
    ],
)
def test_lower_bounds(data: dict[str, Any], field: str) -> None:
    cls = {
        "requirements": c.IntakeOutput,
        "acceptance_criteria": c.Requirement,
        "steps": c.PlanOutput,
        "attempt": c.DebugInput,
        "baseline_version": c.UserProxyInput,
        "criterion": c.Criterion,
    }[field]
    with pytest.raises(ValidationError):
        cls.model_validate(data)


def test_plan_has_at_most_twelve_steps_pointing_backwards() -> None:
    twelve = [with_(STEP, id=f"P{i}", depends_on=[f"P{i - 1}"] if i > 1 else []) for i in range(1, 13)]
    c.PlanOutput.model_validate(with_(VALID[c.PlanOutput], steps=twelve))
    for bad in (
        [with_(STEP, id="P1", depends_on=["P2"]), with_(STEP, id="P2")],  # forward
        [with_(STEP, id="P1", depends_on=["P1"])],  # itself
        [with_(STEP, id="P1"), with_(STEP, id="P2", depends_on=["P3"])],  # unknown
        [with_(STEP, id="P1"), with_(STEP, id="P1")],  # duplicate id
    ):
        with pytest.raises(ValidationError):
            c.PlanOutput.model_validate(with_(VALID[c.PlanOutput], steps=bad))


def test_requirement_and_criterion_keys_are_unique() -> None:
    two = _requirements(2)
    c.IntakeOutput.model_validate(with_(VALID[c.IntakeOutput], requirements=two))
    same_req = [two[0], with_(two[1], key="REQ-1")]
    same_ac = [two[0], with_(two[1], acceptance_criteria=[with_(CRITERION, key="AC-1")])]
    for requirements in (same_req, same_ac):
        with pytest.raises(ValidationError):
            c.IntakeOutput.model_validate(with_(VALID[c.IntakeOutput], requirements=requirements))
    with pytest.raises(ValidationError):
        c.UserProxyOutput.model_validate(
            with_(VALID[c.UserProxyOutput], criteria_results=[CRITERION_RESULT, CRITERION_RESULT])
        )


@pytest.mark.parametrize(
    "finding",
    [
        {k: v for k, v in FINDING.items() if k != "evidence"},
        with_(FINDING, evidence=""),
        with_(FINDING, severity="blocker"),
        with_(FINDING, line_start=5, line_end=4),
        with_(FINDING, file_path=None, line_start=1, line_end=None),
        with_(FINDING, file_path=None, line_start=None, line_end=3),
        with_(FINDING, line_start=0),
    ],
)
def test_review_output_schema(finding: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        c.ReviewOutput.model_validate(with_(VALID[c.ReviewOutput], findings=[finding]))


def test_a_finding_may_name_no_place() -> None:
    finding = with_(FINDING, file_path=None, line_start=None, line_end=None)
    c.ReviewOutput.model_validate(with_(VALID[c.ReviewOutput], findings=[finding]))


def _field_names(cls: type[BaseModel], seen: set[type[BaseModel]]) -> Iterator[str]:
    if cls in seen:
        return
    seen.add(cls)
    for name, info in cls.model_fields.items():
        yield name
        for nested in _models_in(info.annotation):
            yield from _field_names(nested, seen)


def _models_in(annotation: Any) -> Iterator[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for arg in get_args(annotation):
        yield from _models_in(arg)


def test_no_contract_carries_an_approval_or_a_test_status() -> None:
    seen: set[type[BaseModel]] = set()
    names = {name for cls in CONTRACTS for name in _field_names(cls, seen)}
    assert len(seen) > len(CONTRACTS)  # nested classes were walked too
    assert not [n for n in names if "approv" in n or "tests_passed" in n or "passed_tests" in n]


def test_workflow_types_match_the_task_model() -> None:
    assert get_args(c.WorkflowType) == WORKFLOW_TYPES


def test_a_passed_claim_needs_evidence() -> None:
    """Review A07 evidence (CLAUDE.md #1): ``passed`` with no evidence ref is refused by the contract, for a
    criterion and for the report; every other status may name none."""
    c.CriterionResult.model_validate(with_(CRITERION_RESULT, status="passed"))
    with pytest.raises(ValidationError, match="passed needs evidence_refs"):
        c.CriterionResult.model_validate(with_(CRITERION_RESULT, status="passed", evidence_refs=[]))
    for status in ("failed", "blocked", "unverified"):
        c.CriterionResult.model_validate(with_(CRITERION_RESULT, status=status, evidence_refs=[]))
    passed = with_(CRITERION_RESULT, status="passed")
    c.UserProxyOutput.model_validate(
        with_(VALID[c.UserProxyOutput], status="passed", criteria_results=[passed])
    )
    without = with_(CRITERION_RESULT, evidence_refs=[])
    for results in ([], [without]):
        with pytest.raises(ValidationError, match="passed needs evidence_refs"):
            c.UserProxyOutput.model_validate(
                with_(VALID[c.UserProxyOutput], status="passed", criteria_results=results)
            )
    for status in ("needs_changes", "blocked", "unverified"):
        c.UserProxyOutput.model_validate(with_(VALID[c.UserProxyOutput], status=status, criteria_results=[]))
