"""The I/O contracts of the six runtime agents (FR-042, AGENT_CATALOG §7; spec WP-3.1f Keputusan Lead #25).

A runtime agent's input is one ``*Input`` instance; its result is the input of ``submit_result``, validated
against its ``*Output`` class (ADR-013), whose ``model_json_schema()`` is the tool's ``input_schema``. Profile
agents use their runtime agent's pair (AGENT_CATALOG §4.2).

Rules every class follows:
- ``extra="forbid"`` and frozen; every string and list has a maximum length;
- keys follow their patterns (``REQ-n``, ``AC-n``, ``P1``..``P12``, evidence refs USER_PROXY_ACCEPTANCE §4),
  except an A07 criterion's evidence refs, which ``verify_report`` checks (Lead decision 3.1f2 #45);
- a path is relative, inside the worktree, without a control character or a backslash;
- no field claims a test status (only ``test_runs`` does) or an approval (only people decide those);
- validator messages name the rule, never the input.

``OUTPUT_CONTRACTS``/``INPUT_CONTRACTS`` stay module-level dict literals: ``scripts/validate_registry.py``
reads them with ``ast`` (wrapping them, e.g. in ``MappingProxyType``, would hide them from the CI validator).
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    model_validator,
)

UUID_HEX: Final = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# USER_PROXY_ACCEPTANCE §4: test_run/artifact/finding refs, and diff:<task uuid>.
EVIDENCE_REF: Final = r"^(?:test_run|artifact|finding|diff):" + UUID_HEX + r"$"
# Relative, no control character or backslash. No look-around: Pydantic's default Rust regex engine refuses it
# at class creation; a ".." segment is refused by the validator instead.
REPO_PATH: Final = r"^[^/\x00-\x1f\\][^\x00-\x1f\\]*$"


def _inside_worktree(path: str) -> str:
    if ".." in path.split("/"):
        raise ValueError("a path must stay inside the worktree (no '..' segment)")
    return path


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Line = Annotated[str, StringConstraints(min_length=1, max_length=300)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
Title = Annotated[str, StringConstraints(min_length=1, max_length=200)]  # tasks.title varchar(200)
Prompt = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
RepoPath = Annotated[
    str, StringConstraints(min_length=1, max_length=1000, pattern=REPO_PATH), AfterValidator(_inside_worktree)
]
RequirementKey = Annotated[str, StringConstraints(pattern=r"^REQ-[1-9][0-9]{0,2}$")]
CriterionKey = Annotated[str, StringConstraints(pattern=r"^AC-[1-9][0-9]{0,2}$")]
StepId = Annotated[str, StringConstraints(pattern=r"^P(?:[1-9]|1[0-2])$")]
EvidenceRef = Annotated[str, StringConstraints(pattern=EVIDENCE_REF)]
# Lead decision 3.1f2 #45 (USER_PROXY_ACCEPTANCE §5 V2): an A07 criterion's ref is not pattern-checked here.
# verify_report sorts malformed refs out itself (``malformed_ref`` for a passed criterion, dropped for
# another, audited either way); a schema error would refuse the whole submit_result first and lose that, as
# in #36. Still one bounded line: 1-80 characters, no control character.
CriterionEvidenceRef = Annotated[
    str, StringConstraints(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")
]
DiffRef = Annotated[str, StringConstraints(pattern=r"^diff:" + UUID_HEX + r"$")]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Category = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,59}$")]
WorkflowType = Literal["create", "modify", "debug", "test", "review", "docs"]  # == WORKFLOW_TYPES
Level = Literal["low", "medium", "high"]
VerificationMethod = Literal["test", "api", "browser", "review", "manual"]  # DATA_MODEL acceptance_criteria
FailureCategory = Literal["assertion", "lint", "build", "dependency", "runner_crash", "timeout", "infra"]
Severity = Literal["info", "low", "medium", "high", "critical"]  # DATA_MODEL findings
Specialist = Literal["B14", "B09", "B10", "B13", "G35"]  # AGENT_CATALOG §4.2


def _duplicates(values: Iterable[str]) -> bool:
    return any(count > 1 for count in Counter(values).values())


# ------------------------------------------------------------------------------------------------ parts


class Criterion(Contract):
    key: CriterionKey
    criterion: Text
    verification_method: VerificationMethod
    mandatory: bool


class Requirement(Contract):
    key: RequirementKey
    title: Line
    description: Text
    priority: Literal["must", "should", "could"]
    acceptance_criteria: Annotated[list[Criterion], Field(min_length=1, max_length=10)]


class ClarificationQuestion(Contract):
    question: Line
    blocking: bool


class PlanStep(Contract):
    id: StepId
    title: Line
    specialist: Specialist
    description: Text
    depends_on: Annotated[list[StepId], Field(max_length=11)]
    requirement_keys: Annotated[list[RequirementKey], Field(max_length=20)]
    files_hint: Annotated[list[RepoPath], Field(max_length=20)]


class TestPlan(Contract):
    __test__ = False  # not a pytest class

    summary: Text
    criterion_keys: Annotated[list[CriterionKey], Field(max_length=100)]


class ChangedFile(Contract):
    path: RepoPath
    change: Literal["added", "modified", "deleted"]


class FailureReport(Contract):
    category: FailureCategory
    summary: Text
    test_run_ref: EvidenceRef | None = None


class Finding(Contract):
    severity: Severity
    category: Category
    title: Line
    file_path: RepoPath | None = None
    line_start: PositiveInt | None = None
    line_end: PositiveInt | None = None
    evidence: Text
    recommendation: Text

    @model_validator(mode="after")
    def _place(self) -> Self:
        if self.file_path is None and (self.line_start is not None or self.line_end is not None):
            raise ValueError("line_start/line_end need file_path")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end must not be before line_start")
        return self


class CriterionView(Contract):
    key: CriterionKey
    requirement_key: RequirementKey
    criterion: Text
    verification_method: VerificationMethod
    mandatory: bool


class EvidenceItem(Contract):
    ref: EvidenceRef
    label: Line


class CriterionResult(Contract):
    criterion_key: CriterionKey
    status: Literal["passed", "failed", "blocked", "unverified"]
    evidence_refs: Annotated[list[CriterionEvidenceRef], Field(max_length=20)]
    rationale: Text


# ------------------------------------------------------------------------------------------- the six pairs


class IntakeInput(Contract):
    task_id: uuid.UUID
    prompt: Prompt
    workflow_hint: WorkflowType | None = None
    stack_summary: Text | None = None
    user_feedback: Text | None = None


class IntakeOutput(Contract):
    title: Title
    summary: Text
    workflow_type: WorkflowType
    complexity: Level
    risk: Level
    requirements: Annotated[list[Requirement], Field(min_length=1, max_length=20)]
    constraints: Annotated[list[Line], Field(max_length=20)]
    detected_stack_hints: Annotated[list[Line], Field(max_length=10)]
    clarification_questions: Annotated[list[ClarificationQuestion], Field(max_length=5)]

    @model_validator(mode="after")
    def _unique_keys(self) -> Self:
        if _duplicates(r.key for r in self.requirements):
            raise ValueError("requirement keys must be unique")
        # USER_PROXY_ACCEPTANCE §2: an AC key names one criterion in the whole task.
        if _duplicates(ac.key for r in self.requirements for ac in r.acceptance_criteria):
            raise ValueError("acceptance criterion keys must be unique in the task")
        return self


class PlanInput(Contract):
    task_id: uuid.UUID
    workflow_type: WorkflowType
    requirements: Annotated[list[Requirement], Field(min_length=1, max_length=20)]
    context_summary: Text | None = None
    user_feedback: Text | None = None


class PlanOutput(Contract):
    steps: Annotated[list[PlanStep], Field(min_length=1, max_length=12)]
    test_plan: TestPlan
    risks: Annotated[list[Line], Field(max_length=10)]
    needs_architect: bool = False

    @model_validator(mode="after")
    def _a_dag_in_order(self) -> Self:
        earlier: set[str] = set()
        for step in self.steps:
            if step.id in earlier:
                raise ValueError("step ids must be unique")
            if not set(step.depends_on) <= earlier:
                raise ValueError("depends_on may only name earlier steps")
            earlier.add(step.id)
        return self


class ImplementInput(Contract):
    task_id: uuid.UUID
    step: PlanStep
    requirements: Annotated[list[Requirement], Field(max_length=20)]
    gap_feedback: Text | None = None


class ImplementOutput(Contract):
    summary: Text
    changed_files: Annotated[list[ChangedFile], Field(max_length=200)]
    commit: CommitSha | None = None
    evidence_refs: Annotated[list[EvidenceRef], Field(max_length=20)]
    follow_ups: Annotated[list[Line], Field(max_length=10)]


class DebugInput(Contract):
    task_id: uuid.UUID
    failure: FailureReport
    attempt: Annotated[int, Field(ge=1, le=20)]
    requirements: Annotated[list[Requirement], Field(max_length=20)]


class DebugOutput(Contract):
    root_cause: Text
    category: FailureCategory
    fix_summary: Text
    changed_files: Annotated[list[ChangedFile], Field(max_length=200)]
    next_step: Literal["retest", "escalate", "needs_clarification"]
    evidence_refs: Annotated[list[EvidenceRef], Field(max_length=20)]


class ReviewInput(Contract):
    task_id: uuid.UUID
    diff_ref: DiffRef
    changed_files: Annotated[list[RepoPath], Field(max_length=200)]
    requirements: Annotated[list[Requirement], Field(max_length=20)]


class ReviewOutput(Contract):
    summary: Text
    findings: Annotated[list[Finding], Field(max_length=50)]


class UserProxyInput(Contract):
    task_id: uuid.UUID
    baseline_version: PositiveInt
    criteria: Annotated[list[CriterionView], Field(max_length=100)]
    evidence: Annotated[list[EvidenceItem], Field(max_length=200)]
    user_feedback: Text | None = None


class UserProxyOutput(Contract):
    status: Literal["passed", "needs_changes", "blocked", "unverified"]
    summary: Text
    criteria_results: Annotated[list[CriterionResult], Field(max_length=100)]
    next_actions: Annotated[list[Line], Field(max_length=10)]
    confidence: Level

    @model_validator(mode="after")
    def _unique_criteria(self) -> Self:
        if _duplicates(r.criterion_key for r in self.criteria_results):
            raise ValueError("criterion keys must be unique")
        return self


# Dict literals on purpose (module docstring); app.agents.registry.RUNTIME_CONTRACTS names the same pairs.
OUTPUT_CONTRACTS: Mapping[str, type[Contract]] = {
    "A02": IntakeOutput,
    "A03": PlanOutput,
    "B14": ImplementOutput,
    "C20": DebugOutput,
    "D22": ReviewOutput,
    "A07": UserProxyOutput,
}
INPUT_CONTRACTS: Mapping[str, type[Contract]] = {
    "A02": IntakeInput,
    "A03": PlanInput,
    "B14": ImplementInput,
    "C20": DebugInput,
    "D22": ReviewInput,
    "A07": UserProxyInput,
}
