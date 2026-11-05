"""``approvals`` and ``projects.deleted_at`` (DATA_MODEL approvals + ADR-024 columns, migration 0007).

Model ↔ migration parity is covered for the whole metadata by test_agent_tables.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models.approvals import Approval
from app.models.identity import User
from app.models.projects import Project
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


def approval(project: Project, **overrides: object) -> Approval:
    values: dict[str, object] = {
        "project_id": project.id,
        "action_type": "project_delete",
        "risk_level": 3,
        "summary": "Delete project",
        "payload": {"project_id": str(project.id), "name": project.name, "previous_status": "active"},
        "payload_hash": "a" * 64,
        "requested_by": f"user:{project.owner_id}",
        "expires_at": datetime(2026, 11, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return Approval(**values)


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("action_type", "approve_all", "ck_approvals_action_type"),
        ("status", "done", "ck_approvals_status"),
        ("handler_status", "ok", "ck_approvals_handler_status"),
        ("payload_hash", "A" * 64, "ck_approvals_payload_hash"),
        ("requested_by", "agent:x", "ck_approvals_requested_by"),
        ("risk_level", 5, "ck_approvals_risk_level"),
        ("decision_note", "x" * 2001, "ck_approvals_decision_note"),
    ],
)
async def test_enum_checks(session: AsyncSession, field: str, value: object, constraint: str) -> None:
    project = await make_project(session)
    session.add(approval(project, **{field: value}))
    with pytest.raises(IntegrityError, match=constraint):
        await session.flush()


async def test_defaults(session: AsyncSession) -> None:
    project = await make_project(session)
    row = approval(project)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    assert (row.status, row.handler_status, row.handler_attempts) == ("pending", "pending", 0)
    assert row.created_at is not None and row.updated_at is not None


async def test_system_requester_is_accepted(session: AsyncSession) -> None:
    project = await make_project(session)
    session.add(approval(project, requested_by="system:orchestrator"))
    await session.flush()


async def test_one_open_project_delete_per_project(session: AsyncSession) -> None:
    project = await make_project(session)
    session.add(approval(project, status="superseded"))
    session.add(approval(project))
    await session.flush()  # one superseded + one pending is fine
    session.add(approval(project, status="approved"))
    with pytest.raises(IntegrityError, match="uq_approvals_open_project_delete"):
        await session.flush()


@pytest.mark.parametrize(
    ("status", "deleted_at"), [("deleted", None), ("active", datetime(2026, 11, 5, tzinfo=UTC))]
)
async def test_deleted_at_matches_status(
    session: AsyncSession, status: str, deleted_at: datetime | None
) -> None:
    project = await make_project(session)
    project.status, project.deleted_at = status, deleted_at
    with pytest.raises(IntegrityError, match="ck_projects_deleted_at"):
        await session.flush()


async def test_project_delete_cascades_approvals(session: AsyncSession) -> None:
    project = await make_project(session)
    session.add(approval(project))
    await session.flush()
    await session.execute(delete(Project).where(Project.id == project.id))
    assert await session.scalar(select(func.count()).select_from(Approval)) == 0


async def test_decided_by_user_is_restricted(session: AsyncSession) -> None:
    # Who decided must stay on record: a decider cannot be deleted while their decisions exist.
    project = await make_project(session)
    decider = User(email="decider@example.test", display_name="D", password_hash="x")
    session.add(decider)
    await session.flush()
    session.add(approval(project, status="approved", decided_by=decider.id, decided_at=func.now()))
    await session.flush()
    with pytest.raises(IntegrityError, match="fk_approvals_decided_by_users"):
        await session.execute(delete(User).where(User.id == decider.id))
