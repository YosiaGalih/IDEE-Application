"""Approval lifecycle primitives (spec WP-3.1c Goal 3): create, consume once, supersede, expire."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.audit import AuditWriter
from app.core.redaction import Redactor
from app.models.approvals import Approval
from app.models.audit import AuditEvent
from app.models.projects import Project
from app.services import approval_store as store
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project

AUDIT = AuditWriter(Redactor())
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


async def new(
    session: AsyncSession, project: Project, *, name: str = "Demo", now: datetime = NOW
) -> Approval:
    return await store.create(
        session,
        AUDIT,
        project_id=project.id,
        task_id=None,
        action_type="project_delete",
        payload={"project_id": str(project.id), "name": name, "previous_status": "active"},
        summary="Delete project",
        risk_level=3,
        requested_by=f"user:{project.owner_id}",
        now=now,
    )


async def actions(session: AsyncSession, project: Project) -> list[str]:
    # audit_events is append-only: earlier tests' rows stay, so read only this project's.
    query = select(AuditEvent.action).where(AuditEvent.project_id == project.id).order_by(AuditEvent.id)
    return list((await session.scalars(query)).all())


async def test_create_sets_hash_and_expiry(session: AsyncSession) -> None:
    project = await make_project(session)
    approval = await new(session, project)
    assert approval.status == "pending"
    assert approval.expires_at == NOW + timedelta(days=7)
    assert approval.payload_hash == store.payload_hash(
        "project_delete", project_id=project.id, task_id=None, payload=approval.payload
    )
    assert await actions(session, project) == ["approval.requested"]


async def test_waive_criterion_is_never_created(session: AsyncSession) -> None:
    project = await make_project(session)
    with pytest.raises(ValueError, match="waive_criterion"):
        await store.create(
            session,
            AUDIT,
            project_id=project.id,
            task_id=None,
            action_type="waive_criterion",
            payload={},
            summary="x",
            risk_level=3,
            requested_by="system:user_proxy",
            now=NOW,
        )


async def test_consume_once(session: AsyncSession) -> None:
    project = await make_project(session)
    approval = await new(session, project)
    approval.status = "approved"
    await session.flush()
    assert await store.consume(session, approval.id, payload_hash=approval.payload_hash, now=NOW) is True
    assert await store.consume(session, approval.id, payload_hash=approval.payload_hash, now=NOW) is False
    await session.refresh(approval)
    assert (approval.status, approval.consumed_at) == ("consumed", NOW)


async def test_consume_refuses_another_hash(session: AsyncSession) -> None:
    # Consumed only with the hash the decider saw: any other payload is another action.
    project = await make_project(session)
    approval = await new(session, project)
    approval.status = "approved"
    await session.flush()
    assert await store.consume(session, approval.id, payload_hash="b" * 64, now=NOW) is False
    await session.refresh(approval)
    assert approval.status == "approved"


async def test_consume_refuses_after_expiry(session: AsyncSession) -> None:
    project = await make_project(session)
    approval = await new(session, project)
    approval.status = "approved"
    await session.flush()
    assert (
        await store.consume(
            session, approval.id, payload_hash=approval.payload_hash, now=NOW + timedelta(days=7)
        )
        is False
    )


async def test_renew_supersedes_and_keeps_requester(session: AsyncSession) -> None:
    project = await make_project(session)
    old = await new(session, project)
    renewed = await store.renew_open(
        session,
        AUDIT,
        project_id=project.id,
        action_type="project_delete",
        changes={"name": "Renamed"},
        actor_id="user:00000000-0000-4000-8000-000000000001",
        now=NOW + timedelta(hours=1),
    )
    assert renewed is not None and renewed.id != old.id
    await session.refresh(old)
    assert old.status == "superseded"
    assert renewed.payload["name"] == "Renamed" and renewed.payload["previous_status"] == "active"
    assert renewed.requested_by == old.requested_by
    assert renewed.payload_hash != old.payload_hash
    assert renewed.expires_at == NOW + timedelta(hours=1, days=7)
    assert await actions(session, project) == [
        "approval.requested",
        "approval.superseded",
        "approval.requested",
    ]
    renewal = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == str(renewed.id), AuditEvent.action == "approval.requested"
        )
    )
    assert renewal is not None
    # who caused the renewal is the actor; the original requester stays on record (AG-13 LOW-4)
    assert (renewal.actor_type, renewal.actor_id) == ("user", "00000000-0000-4000-8000-000000000001")
    assert renewal.metadata_["requested_by"] == old.requested_by
    assert renewal.metadata_["renewed_from"] == str(old.id)


async def test_renew_without_an_open_approval_does_nothing(session: AsyncSession) -> None:
    project = await make_project(session)
    assert (
        await store.renew_open(
            session,
            AUDIT,
            project_id=project.id,
            action_type="project_delete",
            changes={"name": "x"},
            actor_id="system:test",
            now=NOW,
        )
        is None
    )


async def test_expire_due_only_past_pending(session: AsyncSession) -> None:
    past = await make_project(session, slug="a")
    future = await make_project(session, slug="b")
    decided = await make_project(session, slug="c")
    gone = await new(session, past, now=NOW - timedelta(days=8))
    await new(session, future, now=NOW)
    approved = await new(session, decided, now=NOW - timedelta(days=8))
    approved.status = "approved"
    await session.flush()
    expired = await store.expire_due(session, AUDIT, now=NOW)
    assert expired == [gone.id]
    await session.refresh(gone)
    assert (gone.status, gone.handler_status) == ("expired", "pending")
    assert (await actions(session, past)).count("approval.expired") == 1
    assert "approval.expired" not in await actions(session, decided)


async def test_only_action_types_with_a_handler_can_be_requested(session: AsyncSession) -> None:
    project = await make_project(session)
    with pytest.raises(ValueError, match="no handler"):
        await store.create(
            session,
            AUDIT,
            project_id=project.id,
            task_id=None,
            action_type="tool_r3",
            payload={},
            summary="x",
            risk_level=3,
            requested_by="system:orchestrator",
            now=NOW,
        )


async def test_an_approval_without_expiry_never_expires(session: AsyncSession) -> None:
    # ARCHITECTURE §10: expires_at IS NULL OR expires_at > now() (v1 always sets one; NULL stays consistent).
    project = await make_project(session)
    approval = await new(session, project)
    approval.status, approval.expires_at = "approved", None
    await session.flush()
    later = NOW + timedelta(days=365)
    assert await store.consume(session, approval.id, payload_hash=approval.payload_hash, now=later) is True
