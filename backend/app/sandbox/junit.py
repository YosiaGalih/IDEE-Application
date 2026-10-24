"""JUnit XML from a run's ``/workspace/.kk/junit.xml`` (pytest, node --test, ...).

Counts come from the ``<testcase>`` elements themselves, not from the summary attributes a runner writes:
the summary is a claim, the cases are the evidence (CLAUDE.md #1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.sandbox.safexml import UnsafeXML, parse

CaseStatus = Literal["passed", "failed", "error", "skipped"]
MAX_CASES = 1000  # [Menebak] cases kept in detail; counts always cover every case
MAX_MESSAGE = 2000  # [Menebak]


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

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped


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
        status: CaseStatus = "passed"
        message = ""
        for child in case:
            kind = {"failure": "failed", "error": "error", "skipped": "skipped"}.get(child.tag)
            if kind is not None:
                status = kind  # type: ignore[assignment]
                message = (child.get("message") or child.text or "").strip()[:MAX_MESSAGE]
                break
        counts[status] += 1
        if len(cases) < MAX_CASES:
            cases.append(JUnitCase(case.get("name", ""), case.get("classname", ""), status, message))
    return JUnitSummary(
        tests=sum(counts.values()),
        failures=counts["failed"],
        errors=counts["error"],
        skipped=counts["skipped"],
        cases=cases,
    )
