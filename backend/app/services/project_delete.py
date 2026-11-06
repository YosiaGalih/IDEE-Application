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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.approvals import Approval
from app.models.identity import UserRole
from app.models.projects import Project
from app.services import approval_store
from app.services.approval_handlers import MAX_ATTEMPTS
from app.services.project_tasks import cancel_open_tasks
from app.services.projects import Actor, ProjectEnv, ProjectView, audit_project, authorize, commit_and_view
from app.workflow.states import Actor as WorkflowActor

logger = get_logger(__name__)

ACTION = approval_store.PROJECT_DELETE
_HANDLER_ACTOR = "approval_handlers"
DELETABLE = ("active", "archived")
PURGE_AFTER = timedelta(days=7)
SUMMARY = "Delete project (workspace kept 7 days, then purged)"


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def _payload(project: Project, previous_status: object) -> dict[str, object]:
    """API §11 #6: what the decider sees, and what the handler re-derives before it acts."""
    return {"project_id": str(project.id), "name": project.name, "previous_status": previous_status}


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
            if approval.status == "approved" and approval.handler_attempts >= MAX_ATTEMPTS:
                # Approved, but the deletion keeps failing: "accepted" would promise what is not happening.
                raise AppError(
                    "INVALID_TRANSITION",
                    "The approved deletion of this project failed; an operator must retry it",
                    http_status=409,
                    details=[{"reason": "delete_failed", "approval_id": str(approval.id)}],
                )
        elif project.status in DELETABLE:
            previous = project.status
            project.status = "deleting_pending_approval"
            approval = await approval_store.create(
                session,
                env.audit,
                project_id=project.id,
                task_id=None,
                action_type=ACTION,
                payload=_payload(project, previous),
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
        view = await commit_and_view(env, session, project, permissions)
    return approval, view


class ProjectDeleteHandler:
    """The ``project_delete`` handler (ADR-024): idempotent, one transaction per path, commits itself."""

    def __init__(self, *, sessions: async_sessionmaker[AsyncSession], audit: AuditWriter) -> None:
        self._sessions = sessions
        self._audit = audit

    async def _locked(self, session: AsyncSession, approval_id: uuid.UUID) -> tuple[Approval, Project] | None:
        """Project first, then approval: the order a rename takes too, so the two never deadlock."""
        project_id = await session.scalar(select(Approval.project_id).where(Approval.id == approval_id))
        if project_id is None:
            return None  # purged with its project
        # FOR NO KEY UPDATE, as authorize(lock=True) takes it: rows referencing the project stay insertable.
        project = await session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        approval = await session.scalar(
            select(Approval)
            .where(Approval.id == approval_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return None if project is None or approval is None else (approval, project)

    async def _audit_event(
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
        fallback = previous not in DELETABLE
        project.status = "archived" if fallback else str(previous)
        await self._audit_event(
            session, approval, action, {"restored_status": project.status, "fallback": fallback}
        )

    async def approved(self, approval_id: uuid.UUID, *, now: datetime) -> None:
        async with self._sessions() as session:
            locked = await self._locked(session, approval_id)
            if locked is None:
                return
            approval, project = locked
            if approval.status != "approved":
                return  # consumed by an earlier run, or superseded: nothing to do
            if project.status != "deleting_pending_approval":
                # Nothing should get here; a silent return would be recorded as success (silent M2).
                raise RuntimeError(f"project is {project.status}, not awaiting deletion")
            if approval.expires_at is not None and approval.expires_at <= now:
                # Approved, but its time ran out before it could be used: the request lapses.
                await approval_store.expire(
                    session, self._audit, approval, actor=f"system:{_HANDLER_ACTOR}", now=now
                )
                await self._restore(session, approval, project, "project.delete_expired")
                await session.commit()
                return
            # The hash of the action about to run, from the project as it is now (SECURITY_MODEL §5): never
            # the row's own hash, which would only compare the approval with itself.
            executed = approval_store.payload_hash(
                ACTION,
                project_id=project.id,
                task_id=None,
                payload=_payload(project, approval.payload.get("previous_status")),
            )
            if executed != approval.payload_hash:
                raise RuntimeError("the project no longer matches what was approved")
            if not await approval_store.consume(session, approval.id, payload_hash=executed, now=now):
                raise RuntimeError("the approval could not be consumed")  # decided or used meanwhile
            # WP-INT-04: a deleted project keeps no work running.
            cancelled = await cancel_open_tasks(
                session,
                project.id,
                reason="project deleted",
                actor=WorkflowActor("system", f"approval:{approval.id}"),
                audit=self._audit,
            )
            project.status, project.deleted_at = "deleted", now
            await approval_store.audit_event(
                session, self._audit, approval, "approval.consumed", actor=f"system:{_HANDLER_ACTOR}"
            )
            await self._audit_event(session, approval, "project.deleted", {"cancelled_tasks": cancelled})
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


def _remove_folder(root: Path, project_id: uuid.UUID) -> str:
    """``root/<project_id>`` and nothing else, never through a symlink. Returns what was there."""
    folder = root / str(project_id)
    if folder.is_symlink():
        folder.unlink()  # the link goes, its target stays
        return "symlink"
    if folder.exists():
        shutil.rmtree(folder)  # does not follow symlinks inside (fd-based on Linux)
        return "removed"
    return "absent"


@dataclass(frozen=True, slots=True)
class PurgeResult:
    purged: list[uuid.UUID]
    failed: list[uuid.UUID]


async def _purge_one(
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    workspace_root: Path,
    project_id: uuid.UUID,
) -> bool:
    folder = await asyncio.to_thread(_remove_folder, workspace_root, project_id)
    async with sessions() as session:
        project = await session.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if project is None or project.status != "deleted" or project.deleted_at is None:
            return False
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
            metadata={"deleted_at": project.deleted_at.isoformat(), "workspace": folder},
        )
        await session.execute(delete(Project).where(Project.id == project_id))  # the database cascades
        await session.commit()
    return True


async def _purge_failed(
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    project_id: uuid.UUID,
    exc: BaseException,
    at: datetime,
) -> None:
    logger.error(
        "project_purge_failed", project_id=str(project_id), error_type=type(exc).__name__, exc_info=True
    )
    try:
        async with sessions() as session:
            await audit.write(
                session,
                actor_type="system",
                actor_id="project_purge",
                action="project.purge_failed",
                result="error",
                resource_type="project",
                resource_id=str(project_id),
                project_id=project_id,
                metadata={"error_type": type(exc).__name__},
            )
            # To the back of the queue, so one stuck project does not starve the others.
            await session.execute(update(Project).where(Project.id == project_id).values(updated_at=at))
            await session.commit()
    except Exception:
        logger.error("project_purge_failure_unrecorded", project_id=str(project_id), exc_info=True)


async def purge_deleted(
    *,
    sessions: async_sessionmaker[AsyncSession],
    audit: AuditWriter,
    workspace_root: Path,
    now: datetime | None = None,
    limit: int = 20,
) -> PurgeResult:
    """Projects deleted 7 days ago or more: workspace first (a failure leaves the row to retry), then grants
    and the row (cascade: repository, index, tasks, runs, approvals). The audit trail stays (ADR-005). One
    project's failure is logged and audited and never stops the others."""
    at = _now(now)
    if not await asyncio.to_thread(workspace_root.is_dir):  # unmounted: rows would go, files would stay
        raise RuntimeError("workspace root is not a directory; nothing purged")
    async with sessions() as session:
        due = (
            await session.scalars(
                select(Project.id)
                .where(Project.status == "deleted", Project.deleted_at <= at - PURGE_AFTER)
                .order_by(Project.updated_at)
                .limit(limit)
            )
        ).all()
    purged: list[uuid.UUID] = []
    failed: list[uuid.UUID] = []
    for project_id in due:
        try:
            if await _purge_one(sessions, audit, workspace_root, project_id):
                purged.append(project_id)
        except Exception as exc:  # one project's disk or database trouble is not every project's
            failed.append(project_id)
            await _purge_failed(sessions, audit, project_id, exc, at)
    return PurgeResult(purged=purged, failed=failed)
