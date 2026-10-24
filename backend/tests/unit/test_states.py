"""Task state machine matrix (ARCHITECTURE §5.1 diagram; ADR-006, ADR-021)."""

from __future__ import annotations

import pytest

from app.models.enums import TASK_STATUSES
from app.workflow.states import ALLOWED, TERMINAL, TaskStatus

# Copied edge by edge from the ARCHITECTURE §5.1 diagram, independently of the implementation.
DIAGRAM: dict[str, set[str]] = {
    "draft": {"planned", "needs_clarification", "failed", "cancelled", "waiting_approval"},
    "needs_clarification": {"draft", "cancelled"},
    "planned": {"queued", "needs_clarification", "cancelled"},
    "queued": {"running", "cancelled"},
    "running": {"validating", "waiting_approval", "failed", "interrupted", "cancelled"},
    "validating": {"completed", "needs_changes", "waiting_approval", "failed", "interrupted", "cancelled"},
    "needs_changes": {"queued", "waiting_approval", "cancelled"},
    "waiting_approval": {"queued", "completed", "rejected", "cancelled", "needs_changes", "failed", "draft"},
    "interrupted": {"queued", "failed", "cancelled"},
    "failed": {"queued", "cancelled"},
    "completed": {"queued"},
    "cancelled": set(),
    "rejected": set(),
}


def test_statuses_match_the_schema() -> None:
    assert {s.value for s in TaskStatus} == set(TASK_STATUSES) == set(DIAGRAM)


@pytest.mark.parametrize("source", sorted(DIAGRAM))
@pytest.mark.parametrize("target", sorted(DIAGRAM))
def test_allowed_matches_architecture_diagram(source: str, target: str) -> None:
    assert (TaskStatus(target) in ALLOWED[TaskStatus(source)]) is (target in DIAGRAM[source])


def test_terminal_states_have_no_exits() -> None:
    assert {TaskStatus.CANCELLED, TaskStatus.REJECTED} == TERMINAL
    assert all(not ALLOWED[s] for s in TERMINAL)


def test_adr_006_and_021_edges_present() -> None:
    adr_edges = [
        ("running", "cancelled"),
        ("validating", "cancelled"),
        ("validating", "interrupted"),
        ("needs_clarification", "draft"),
        ("draft", "needs_clarification"),
        ("draft", "failed"),
        ("needs_changes", "waiting_approval"),
        ("completed", "queued"),
        ("waiting_approval", "needs_changes"),
        ("waiting_approval", "failed"),
        ("draft", "waiting_approval"),
        ("waiting_approval", "draft"),
    ]
    for source, target in adr_edges:
        assert TaskStatus(target) in ALLOWED[TaskStatus(source)], (source, target)
