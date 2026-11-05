"""Deleting a project: an R3 action behind a ``project_delete`` approval (FR-018, API §11 #6/#14, §11.1;
spec WP-3.1c Goal 5).

``request_delete`` asks; nothing is deleted until an authorised person approves exactly that request. The
handler then cancels the project's open tasks and marks it ``deleted`` (gone from every list and route);
``purge_deleted`` removes its workspace and rows 7 days later (ADR-005). A rejected or expired request puts
the project back where it was.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.approvals import Approval
from app.models.identity import UserRole
from app.models.projects import Project
from app.models.tasks import Task
from app.services import approval_store
from app.services.projects import Actor, ProjectEnv, ProjectView, audit_project, authorize, committed_view
from app.workflow.states import ALLOWED, TaskStatus, transition
from app.workflow.states import Actor as WorkflowActor

logger = get_logger(__name__)

ACTION = "project_delete"
DELETABLE = ("active", "archived")
PURGE_AFTER = timedelta(days=7)
SUMMARY = "Delete project (workspace kept 7 days, then purged)"
_CANCELLABLE = tuple(status.value for status, targets in ALLOWED.items() if TaskStatus.CANCELLED in targets)


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _invalid(project: Project) -> AppError:
    return AppError(
        "INVALID_TRANSITION",
        f"Project cannot be deleted while {project.status}",
        http_status=409,
        details=[{"from": project.status, "to": "deleting_pending_approval"}],
    )


async def request_delete(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[Approval, ProjectView]:
    """``active``/``archived`` → ``deleting_pending_approval`` with a pending approval (202 in the API).
    Asking again returns the open approval."""
    async with env.sessions() as session:
        project, permissions = await authorize(session, actor, project_id, "project:delete", lock=True)
        if project.status == "deleting_pending_approval":
            approval = await approval_store.find_open(session, project_id=project.id, action_type=ACTION)
            if approval is None:  # the last request expired or was rejected; its handler has not restored yet
                raise _invalid(project)
        elif project.status in DELETABLE:
            previous = project.status
            project.status = "deleting_pending_approval"
            approval = await approval_store.create(
                session,
                env.audit,
                project_id=project.id,
                task_id=None,
                action_type=ACTION,
                payload={"project_id": str(project.id), "name": project.name, "previous_status": previous},
                summary=SUMMARY,
                risk_level=3,
                requested_by=f"user:{actor.user_id}",
                actor_ip=actor.ip,
                now=now,
            )
            await audit_project(
                env,
                session,
                actor,
                project,
                "project.delete_requested",
                {"approval_id": str(approval.id), "previous_status": previous},
            )
        else:
            raise _invalid(project)
        view = await committed_view(env, session, project, permissions)
    return approval, view


class ProjectDeleteHandler:
    """The ``project_delete`` handler (ADR-024): idempotent, one transaction per path, commits itself."""

    def __init__(self, *, sessions: async_sessionmaker[AsyncSession], audit: AuditWriter) -> None:
        self._sessions = sessions
        self._audit = audit

    async def _locked(self, session: AsyncSession, approval_id: uuid.UUID) -> tuple[Approval, Project] | None:
        approval = await session.get(Approval, approval_id, with_for_update=True)
        if approval is None:
            return None  # purged with its project
        project = await session.scalar(
            select(Project).where(Project.id == approval.project_id).with_for_update()
        )
        return None if project is None else (approval, project)

    async def _write(
        self, session: AsyncSession, approval: Approval, action: str, metadata: dict[str, object]
    ) -> None:
        await self._audit.write(
            session,
            actor_type="system",
            actor_id=f"approval:{approval.id}",
            action=action,
            result="success",
            resource_type="project",
            resource_id=str(approval.project_id),
            project_id=approval.project_id,
            metadata={"approval_id": str(approval.id), **metadata},
        )

    async def _restore(
        self, session: AsyncSession, approval: Approval, project: Project, action: str
    ) -> None:
        if project.status != "deleting_pending_approval":
            return  # already settled
        if await approval_store.find_open(session, project_id=project.id, action_type=ACTION) is not None:
            return  # a newer request (after a rename) is still open
        previous = approval.payload.get("previous_status")
        project.status = previous if previous in DELETABLE else "archived"
        await self._write(session, approval, action, {"restored_status": project.status})

    async def approved(self, approval_id: uuid.UUID, *, now: datetime) -> None:
        async with self._sessions() as session:
            locked = await self._locked(session, approval_id)
            if locked is None:
                return
            approval, project = locked
            if approval.status != "approved" or project.status != "deleting_pending_approval":
                return  # consumed by an earlier run, or superseded: nothing to do
            if not await approval_store.consume(
                session, approval.id, payload_hash=approval.payload_hash, now=now
            ):
                # Approved, but its time ran out before it could be used: the request lapses.
                await approval_store.expire(session, self._audit, approval, actor="system:approval_handlers")
                await self._restore(session, approval, project, "project.delete_expired")
                await session.commit()
                return
            tasks = (
                await session.scalars(
                    select(Task)
                    .where(Task.project_id == project.id, Task.status.in_(_CANCELLABLE))
                    .with_for_update()
                )
            ).all()
            for task in tasks:  # WP-INT-04: a deleted project keeps no work running
                await transition(
                    session,
                    task,
                    TaskStatus.CANCELLED,
                    reason="project deleted",
                    actor=WorkflowActor("system", f"approval:{approval.id}"),
                    audit=self._audit,
                )
            project.status, project.deleted_at = "deleted", now
            await self._write(session, approval, "approval.consumed", {"payload_hash": approval.payload_hash})
            await self._write(session, approval, "project.deleted", {"cancelled_tasks": len(tasks)})
            await session.commit()

    async def rejected(self, approval_id: uuid.UUID, *, now: datetime) -> None:
        await self._settle(approval_id, "project.delete_rejected")

    async def expired(self, approval_id: uuid.UUID, *, now: datetime) -> None:
        await self._settle(approval_id, "project.delete_expired")

    async def _settle(self, approval_id: uuid.UUID, action: str) -> None:
        async with self._sessions() as session:
            locked = await self._locked(session, approval_id)
            if locked is not None:
                await self._restore(session, *locked, action)
            await session.commit()


def _remove_folder(root: Path, project_id: uuid.UUID) -> None:
    """``root/<project_id>`` and nothing else: never through a symlink, never outside ``root``."""
    folder = root / str(project_id)
    if folder.parent.resolve() != root.resolve():
        raise RuntimeError("workspace root is not a plain directory")
    if folder.is_symlink():
        folder.unlink()  # the link goes, its target stays
    elif folder.exists():
        shutil.rmtree(folder)  # does not follow symlinks inside (fd-based on Linux)


async def purge_deleted(
    *,
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    workspace_root: Path,
    now: datetime | None = None,
    limit: int = 20,
) -> list[uuid.UUID]:
    """Projects deleted 7 days ago or more: workspace first (a failure leaves the row to retry), then grants
    and the row (cascade: repository, index, tasks, runs, approvals). The audit trail stays (ADR-005)."""
    cutoff = _now(now) - PURGE_AFTER
    async with sessions() as session:
        due = (
            await session.scalars(
                select(Project.id)
                .where(Project.status == "deleted", Project.deleted_at <= cutoff)
                .order_by(Project.deleted_at)
                .limit(limit)
            )
        ).all()
    purged: list[uuid.UUID] = []
    for project_id in due:
        await asyncio.to_thread(_remove_folder, workspace_root, project_id)
        async with sessions() as session:
            project = await session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if project is None or project.status != "deleted" or project.deleted_at is None:
                continue
            await session.execute(delete(UserRole).where(UserRole.scope == f"project:{project_id}"))
            await audit.write(
                session,
                actor_type="system",
                actor_id="project_purge",
                action="project.purged",
                result="success",
                resource_type="project",
                resource_id=str(project_id),
                project_id=project_id,
                metadata={"deleted_at": project.deleted_at.isoformat()},
            )
            await session.execute(delete(Project).where(Project.id == project_id))  # the database cascades
            await session.commit()
        purged.append(project_id)
    return purged
