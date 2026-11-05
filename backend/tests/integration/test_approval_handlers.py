"""Handler dispatch and the retry sweep (ADR-024; spec WP-3.1c Goal 4): a decision is never undone by a
failing handler, and a failed handler is retried with backoff, then given up on visibly."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.models.approvals import Approval
from app.models.audit import AuditEvent
from app.services import approval_handlers as handlers
from app.services import approval_store as store
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project

AUDIT = AuditWriter(Redactor())
NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


class Fake:
    """Records every call; fails the first ``fail`` calls."""

    def __init__(self, fail: int = 0) -> None:
        self.calls: list[tuple[str, uuid.UUID]] = []
        self.fail = fail

    async def _run(self, path: str, approval_id: uuid.UUID) -> None:
        self.calls.append((path, approval_id))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("database went away")

    async def approved(self, approval_id: uuid.UUID, *, now: datetime) -> None:
        await self._run("approved", approval_id)

    async def rejected(self, approval_id: uuid.UUID, *, now: datetime) -> None:
        await self._run("rejected", approval_id)

    async def expired(self, approval_id: uuid.UUID, *, now: datetime) -> None:
        await self._run("expired", approval_id)


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_sessionmaker(engine)


async def decided(
    sessions: async_sessionmaker[AsyncSession], status: str = "approved", *, created: datetime = NOW
) -> uuid.UUID:
    async with sessions() as session:
        project = await make_project(session)
        approval = await store.create(
            session,
            AUDIT,
            project_id=project.id,
            task_id=None,
            action_type="project_delete",
            payload={"project_id": str(project.id), "name": "Demo", "previous_status": "active"},
            summary="Delete project",
            risk_level=3,
            requested_by=f"user:{project.owner_id}",
            now=created,
        )
        approval.status = status
        await session.commit()
        return approval.id


async def row(sessions: async_sessionmaker[AsyncSession], approval_id: uuid.UUID) -> Approval:
    async with sessions() as session:
        found = await session.get(Approval, approval_id)
        assert found is not None
        return found


async def audits(sessions: async_sessionmaker[AsyncSession], approval_id: uuid.UUID, action: str) -> int:
    # audit_events is append-only: count only this approval's rows.
    async with sessions() as session:
        query = (
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == action, AuditEvent.resource_id == str(approval_id))
        )
        return await session.scalar(query) or 0


async def test_dispatch_success_marks_succeeded(sessions: async_sessionmaker[AsyncSession]) -> None:
    approval_id = await decided(sessions)
    fake = Fake()
    result = await handlers.dispatch(
        sessions=sessions, audit=AUDIT, handlers={"project_delete": fake}, approval_id=approval_id, now=NOW
    )
    assert result == "succeeded"
    assert fake.calls == [("approved", approval_id)]
    approval = await row(sessions, approval_id)
    assert (approval.handler_status, approval.handler_attempts, approval.status) == (
        "succeeded",
        1,
        "approved",
    )


@pytest.mark.parametrize(
    ("status", "path"), [("rejected", "rejected"), ("expired", "expired"), ("consumed", "approved")]
)
async def test_dispatch_picks_the_path_from_the_status(
    sessions: async_sessionmaker[AsyncSession], status: str, path: str
) -> None:
    # consumed but not marked succeeded: the handler committed and the process died before the mark.
    approval_id = await decided(sessions, status)
    fake = Fake()
    await handlers.dispatch(
        sessions=sessions, audit=AUDIT, handlers={"project_delete": fake}, approval_id=approval_id, now=NOW
    )
    assert fake.calls == [(path, approval_id)]


async def test_failed_handler_keeps_decision_and_sweep_finishes_it(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    approval_id = await decided(sessions)
    fake = Fake(fail=1)
    registry = {"project_delete": fake}
    assert (
        await handlers.dispatch(
            sessions=sessions, audit=AUDIT, handlers=registry, approval_id=approval_id, now=NOW
        )
        == "failed"
    )
    approval = await row(sessions, approval_id)
    assert (approval.status, approval.handler_status, approval.handler_attempts) == ("approved", "failed", 1)
    assert approval.handler_error is not None and "RuntimeError" in approval.handler_error
    assert await audits(sessions, approval_id, "approval.handler_failed") == 1
    early = await handlers.sweep(
        sessions=sessions, audit=AUDIT, handlers=registry, now=NOW + timedelta(seconds=29)
    )
    assert early.dispatched == 0 and len(fake.calls) == 1  # still inside the 30 s backoff
    late = await handlers.sweep(
        sessions=sessions, audit=AUDIT, handlers=registry, now=NOW + timedelta(seconds=31)
    )
    assert late.dispatched == 1
    assert (await row(sessions, approval_id)).handler_status == "succeeded"


async def test_gives_up_after_five(sessions: async_sessionmaker[AsyncSession]) -> None:
    approval_id = await decided(sessions)
    fake = Fake(fail=99)
    registry = {"project_delete": fake}
    await handlers.dispatch(
        sessions=sessions, audit=AUDIT, handlers=registry, approval_id=approval_id, now=NOW
    )
    at = NOW
    for _ in range(6):
        at += timedelta(hours=2)
        await handlers.sweep(sessions=sessions, audit=AUDIT, handlers=registry, now=at)
    approval = await row(sessions, approval_id)
    assert (approval.handler_status, approval.handler_attempts) == ("failed", handlers.MAX_ATTEMPTS)
    assert len(fake.calls) == handlers.MAX_ATTEMPTS
    assert await audits(sessions, approval_id, "approval.handler_gave_up") == 1


async def test_sweep_expires_and_dispatches_expired(sessions: async_sessionmaker[AsyncSession]) -> None:
    approval_id = await decided(sessions, "pending", created=NOW - timedelta(days=8))
    fake = Fake()
    result = await handlers.sweep(sessions=sessions, audit=AUDIT, handlers={"project_delete": fake}, now=NOW)
    assert (result.expired, result.dispatched) == (1, 1)
    assert fake.calls == [("expired", approval_id)]
    approval = await row(sessions, approval_id)
    assert (approval.status, approval.handler_status) == ("expired", "succeeded")


async def test_unknown_action_type_fails_visibly(sessions: async_sessionmaker[AsyncSession]) -> None:
    approval_id = await decided(sessions)
    result = await handlers.dispatch(
        sessions=sessions, audit=AUDIT, handlers={}, approval_id=approval_id, now=NOW
    )
    assert result == "failed"
    assert "no handler" in ((await row(sessions, approval_id)).handler_error or "")


async def test_pending_and_superseded_are_not_dispatched(sessions: async_sessionmaker[AsyncSession]) -> None:
    pending = await decided(sessions, "pending")
    fake = Fake()
    await handlers.dispatch(
        sessions=sessions, audit=AUDIT, handlers={"project_delete": fake}, approval_id=pending, now=NOW
    )
    assert fake.calls == []
