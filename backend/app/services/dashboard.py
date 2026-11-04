"""Dashboard (WP-2.1e; FR-007..011, API_CONTRACTS §3 ``GET /dashboard``): counts and lists from stored
projects and tasks, in the user's scope (AC-01). Scope = ``visible_projects_filter``, the rule of
``GET /projects``.

Read only: this module never writes ``tasks.status`` (CLAUDE.md #4; tests/unit/test_status_writer_guard.py).
What the platform does not record yet (agent runs, usage) is reported as unavailable, never as 0 (AC-16).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import Project, Repository
from app.models.tasks import Task
from app.services.projects import Actor, ProjectEnv, ProjectView, project_views, visible_projects_filter

RECENT_PROJECTS = 5
RECENT_TASKS = 10
FAILED_QUEUE = 10
# Work that is executing: validating still runs (tests, review); queued has not started (spec §1).
RUNNING_STATUSES = ("running", "validating")
# The attention queue of FR-010.
ATTENTION_STATUSES = ("failed", "interrupted", "needs_changes", "waiting_approval")
# No source yet: agent_runs and usage_records come with WP-3.1. The screen says "tidak tersedia".
UNAVAILABLE = ("agent_activity", "usage")


@dataclass(frozen=True)
class TaskSummaryView:
    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    title: str
    status: str
    status_reason: str | None
    current_step: str | None
    workflow_type: str
    priority: str
    iteration: int
    created_at: datetime
    updated_at: datetime

    @property
    def has_pending_approval(self) -> bool:
        # Until the approvals table exists (WP-3.1), the task status is the only record of a pending approval.
        return self.status == "waiting_approval"


@dataclass(frozen=True)
class Dashboard:
    metrics: dict[str, int]
    recent_projects: list[ProjectView]
    recent_tasks: list[TaskSummaryView]
    failed_queue: list[TaskSummaryView]
    failed_queue_total: int
    period: tuple[datetime, datetime]


def current_period(now: datetime) -> tuple[datetime, datetime]:
    """The usage period: the calendar month of ``now`` in UTC, ``[start, end)``
    (``cost_budgets.period`` monthly)."""
    now = now.astimezone(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = datetime(now.year + now.month // 12, now.month % 12 + 1, 1, tzinfo=UTC)
    return start, end


_SUMMARY_COLUMNS = (
    Task.id,
    Task.project_id,
    Project.name,
    Task.title,
    Task.status,
    Task.status_reason,
    Task.current_step,
    Task.workflow_type,
    Task.priority,
    Task.iteration,
    Task.created_at,
    Task.updated_at,
)


async def _summaries(
    session: AsyncSession, visible: ColumnElement[bool], *where: ColumnElement[bool], limit: int
) -> list[TaskSummaryView]:
    statement = (
        select(*_SUMMARY_COLUMNS)
        .join(Project, Project.id == Task.project_id)
        .where(visible, *where)
        # Tasks touched in one transaction share updated_at: the newer one first (backend review NIT 1).
        .order_by(Task.updated_at.desc(), Task.created_at.desc(), Task.id)
        .limit(limit)
    )
    return [TaskSummaryView(*row) for row in (await session.execute(statement)).all()]


async def load_dashboard(env: ProjectEnv, actor: Actor, *, now: datetime | None = None) -> Dashboard:
    visible = visible_projects_filter(actor)
    async with env.sessions() as session:
        # One read-only snapshot: the counts, the lists and the queue total agree with each other.
        await session.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )
        active = (
            await session.execute(
                select(func.count()).select_from(Project).where(visible, Project.status == "active")
            )
        ).scalar_one()
        counted = await session.execute(
            select(Task.status, func.count())
            .join(Project, Project.id == Task.project_id)
            .where(visible)
            .group_by(Task.status)
        )
        by_status: dict[str, int] = {status: count for status, count in counted.all()}
        recent_tasks = await _summaries(session, visible, limit=RECENT_TASKS)
        failed_queue = await _summaries(
            session, visible, Task.status.in_(ATTENTION_STATUSES), limit=FAILED_QUEUE
        )
        # The same rows and order as GET /projects (status active), read in this snapshot (backend SHOULD 1).
        project_rows = (
            await session.execute(
                select(Project, Repository)
                .outerjoin(Repository, Repository.project_id == Project.id)
                .where(visible, Project.status == "active")
                .order_by(Project.updated_at.desc(), Project.id)
                .limit(RECENT_PROJECTS)
            )
        ).all()
    # The look at the disk (missing repository, stalled import) needs no transaction.
    recent_projects = await project_views(
        env, actor, [(project, repository) for project, repository in project_rows]
    )
    metrics = {
        "active_projects": active,
        "running_tasks": sum(by_status.get(status, 0) for status in RUNNING_STATUSES),
        "completed_tasks": by_status.get("completed", 0),
        "failed_tasks": by_status.get("failed", 0),
        "waiting_approval": by_status.get("waiting_approval", 0),
    }
    return Dashboard(
        metrics=metrics,
        recent_projects=recent_projects,
        recent_tasks=recent_tasks,
        failed_queue=failed_queue,
        failed_queue_total=sum(by_status.get(status, 0) for status in ATTENTION_STATUSES),
        period=current_period(now or datetime.now(UTC)),
    )
