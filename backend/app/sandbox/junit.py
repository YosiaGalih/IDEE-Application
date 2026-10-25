"""JUnit XML from a run's ``/workspace/.kk/junit.xml`` (pytest, node --test, ...).

**This report is a claim by the project, not evidence.** The test code that ran is untrusted and can write
any report (and exit 0). The evidence is the run's exit code from the manager; a report may refine it, never
overrule it: a non-zero exit stays a failure whatever the report says (CLAUDE.md #1).

Within that limit the parser stays honest about what the report says:
- counts come from the ``<testcase>`` elements, each counted once with its worst outcome
  (error > failed > skipped > passed);
- ``<error>``/``<failure>`` directly under a suite (collection errors) count as errors;
- ``inconsistent`` is set when the report's own summary attributes disagree with its cases;
- detailed cases keep the non-passing ones first; ``cases_truncated`` says when some were left out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from xml.etree.ElementTree import Element

from app.sandbox.safexml import UnsafeXML, parse

CaseStatus = Literal["passed", "failed", "error", "skipped"]
MAX_CASES = 1000  # [Menebak] cases kept in detail; counts always cover every case
MAX_MESSAGE = 2000  # [Menebak]
_OUTCOMES: dict[str, CaseStatus] = {"error": "error", "failure": "failed", "skipped": "skipped"}
_RANK: dict[CaseStatus, int] = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}


class JUnitError(ValueError):
    pass


@dataclass(frozen=True)
class JUnitCase:
    name: str
    classname: str
    status: CaseStatus
    message: str = ""


@dataclass(frozen=True)
class JUnitSummary:
    tests: int
    failures: int
    errors: int
    skipped: int
    cases: list[JUnitCase] = field(default_factory=list)
    cases_truncated: bool = False
    inconsistent: bool = False  # the report's summary attributes do not match its cases

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped


def _outcome(element: Element) -> tuple[CaseStatus, str]:
    status: CaseStatus = "passed"
    message = ""
    for child in element:
        kind = _OUTCOMES.get(child.tag)
        if kind is not None and _RANK[kind] > _RANK[status]:
            status = kind
            message = (child.get("message") or child.text or "").strip()[:MAX_MESSAGE]
    return status, message


def _claimed(root: Element) -> tuple[int, int] | None:
    """(tests, failures + errors) from the top-level summary attributes, when present and numeric."""
    suites = [root] if root.tag == "testsuite" else [s for s in root if s.tag == "testsuite"]
    if root.tag == "testsuites" and root.get("tests") is not None:
        suites = [root]
    try:
        tests = sum(int(s.get("tests", "0")) for s in suites)
        bad = sum(int(s.get("failures", "0")) + int(s.get("errors", "0")) for s in suites)
    except ValueError:
        return None
    return tests, bad


def parse_junit(data: bytes) -> JUnitSummary:
    try:
        root = parse(data)
    except UnsafeXML as exc:
        raise JUnitError(str(exc)) from exc
    if root.tag not in ("testsuites", "testsuite"):
        raise JUnitError("not a JUnit report")
    counts: dict[CaseStatus, int] = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    cases: list[JUnitCase] = []
    for case in root.iter("testcase"):
        status, message = _outcome(case)
        counts[status] += 1
        cases.append(JUnitCase(case.get("name", ""), case.get("classname", ""), status, message))
    for suite in (e for e in root.iter() if e.tag in ("testsuite", "testsuites")):
        for child in suite:
            if child.tag in ("error", "failure"):  # outside any testcase: e.g. a collection error
                counts["error"] += 1
                message = (child.get("message") or child.text or "").strip()[:MAX_MESSAGE]
                cases.append(JUnitCase(suite.get("name", ""), "", "error", message))
    tests = sum(counts.values())
    claimed = _claimed(root)
    inconsistent = claimed is None or claimed != (tests, counts["failed"] + counts["error"])
    if not root.attrib and not any(s.attrib for s in root.iter("testsuite")):
        inconsistent = False  # a report without summary attributes claims nothing
    cases.sort(key=lambda c: -_RANK[c.status])  # stable: document order within an outcome
    return JUnitSummary(
        tests=tests,
        failures=counts["failed"],
        errors=counts["error"],
        skipped=counts["skipped"],
        cases=cases[:MAX_CASES],
        cases_truncated=len(cases) > MAX_CASES,
        inconsistent=inconsistent,
    )
