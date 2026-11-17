"""Tasks: create (API_CONTRACTS §5; spec WP-3.2 §8, §12) and the ``Task`` view. Framework-free.

**Gate.** Until the workflow engine exists (WP-3.2b flips ``RUNTIMES_AVAILABLE["workflow_engine"]``) a task
cannot be created: nothing could ever run it. The gate is read through the module attribute, so a test can
open it. A task is created ``draft``; no job is enqueued here.

**Copied, not referenced.** The effective budget (task, then project, then env) is copied into
``tasks.budget_limit`` at creation, so a later change of the project default never moves a running task.
Prompt and title are redacted before they are stored (SECURITY_MODEL §6).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import readiness
from app.core.errors import AppError
from app.models.approvals import Approval
from app.models.identity import User
from app.models.projects import Project, Repository
from app.models.tasks import Task
from app.services import approvals
from app.services.approvals import ApprovalView
from app.services.context_refs import ContextRef, validate_refs
from app.services.project_budget import BUDGET_SETTINGS_KEY
from app.services.projects import (
    PAGE_SIZE_MAX,
    Actor,
    ProjectEnv,
    authorize,
    has_hidden_characters,
    not_found,
    permissions_for,
    repo_path,
)
from app.services.usage import EMPTY_TOTALS, UsageTotals, task_usage
from app.workflow.budget import (
    BudgetConfigError,
    BudgetDefaults,
    BudgetFields,
    dump_budget,
    effective_budget,
    parse_budget,
)
from app.workspace.fs import Workspace

PROMPT_MAX: Final = 20_000
TITLE_MAX: Final = 200
DEFAULT_WORKFLOW_TYPE: Final = "modify"
DEFAULT_PRIORITY: Final = "normal"
AUDIT_ACTION: Final = "task.created"

Source = Literal["api", "chat"]


@dataclass(frozen=True)
class TaskCreate:
    prompt: str
    title: str | None
    workflow_type: str | None
    priority: str | None
    budget: object | None
    context_refs: Sequence[ContextRef]


@dataclass(frozen=True)
class TaskView:
    task: Task
    usage: UsageTotals
    plan: object | None
    clarification_questions: list[Any]
    pending_approval: ApprovalView | None
    latest_acceptance: None
    last_event_seq: int
    error: dict[str, str | None] | None
    created_by: dict[str, Any]
    iteration_max: int
    cancel_requested: bool
    context_refs: list[dict[str, Any]]


@dataclass(frozen=True)
class TaskSummary:
    """One row of the task list (§11 #2): no prompt, plan or budget."""

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    status: str
    status_reason: str | None
    current_step: str | None
    workflow_type: str
    priority: str
    iteration: int
    usage: UsageTotals
    has_pending_approval: bool
    created_at: datetime
    updated_at: datetime


def _conflict(reason: str, message: str) -> AppError:
    return AppError("CONFLICT", message, http_status=409, details=[{"reason": reason}])


def _invalid(field: str, message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": ["body", field], "msg": message}],
    )


def ensure_engine_available() -> None:
    if not readiness.RUNTIMES_AVAILABLE["workflow_engine"]:
        raise AppError(
            "DEPENDENCY_UNAVAILABLE",
            "The workflow engine is not available yet",
            http_status=503,
            details=[{"reason": "workflow_engine_unavailable"}],
        )


async def lock_project_for_task(session: AsyncSession, actor: Actor, project_id: uuid.UUID) -> Project:
    """The project, locked, when it can take a task now: ``task:create``, active, repository ready."""
    project, _ = await authorize(session, actor, project_id, "task:create", lock=True)
    if project.status == "archived":
        raise _conflict("project_archived", "The project is archived")
    if project.status != "active":
        raise _conflict("project_not_active", "The project is not active")
    sync_state = await session.scalar(
        select(Repository.sync_state).where(Repository.project_id == project.id)
    )
    if sync_state != "ready":
        raise _conflict("repository_not_ready", "The repository is not ready")
    return project


def derive_title(redacted_prompt: str) -> str:
    """The first non-empty line, whitespace collapsed, cut at ``TITLE_MAX``."""
    for line in redacted_prompt.splitlines():
        collapsed = " ".join(line.split())
        if collapsed:
            return collapsed[:TITLE_MAX]
    return ""


def clean_prompt_text(text: str, *, field: str) -> str:
    """Free text the user typed (a prompt, a chat message): CRLF and CR become LF, the ends are stripped, and
    the result is 1..``PROMPT_MAX`` characters of plain text (LF and tab allowed; NUL, other control and
    format characters refused). A 422 at ``["body", field]`` otherwise."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not 1 <= len(cleaned) <= PROMPT_MAX:
        raise _invalid(field, f"must be 1-{PROMPT_MAX} characters")
    if has_hidden_characters(cleaned.replace("\n", "").replace("\t", "")):
        raise _invalid(field, "must be plain text")
    return cleaned


def _checked_text(body: TaskCreate) -> tuple[str, str | None]:
    """(prompt, title): the prompt through ``clean_prompt_text``, the title one plain line."""
    prompt = clean_prompt_text(body.prompt, field="prompt")
    title = body.title.strip() if body.title is not None else None
    if title is not None and (not 1 <= len(title) <= TITLE_MAX or has_hidden_characters(title)):
        raise _invalid("title", f"must be one plain line of 1-{TITLE_MAX} characters")
    return prompt, title


async def insert_task(
    session: AsyncSession,
    env: ProjectEnv,
    actor: Actor,
    project: Project,
    body: TaskCreate,
    *,
    defaults: BudgetDefaults,
    source: Source,
    conversation_id: uuid.UUID | None = None,
    refs: list[dict[str, object]] | None = None,
) -> Task:
    """The ``draft`` row and its audit event, in the caller's transaction (no commit). ``refs`` are the
    caller's validated context refs (chat validates them once for the message and the task)."""
    prompt, title = _checked_text(body)
    try:
        requested = parse_budget(body.budget)
    except BudgetConfigError as exc:
        raise _invalid("budget", str(exc)) from None
    try:
        project_fields = parse_budget(project.settings.get(BUDGET_SETTINGS_KEY))
    except BudgetConfigError:
        raise _conflict("project_budget_invalid", "The project's stored budget cannot be read") from None
    budget = dump_budget(BudgetFields(**asdict(effective_budget(requested, project_fields, defaults))))

    redactor = env.audit.redactor
    redacted_prompt = redactor.redact(prompt)
    shown_title = redactor.redact(title)[:TITLE_MAX] if title is not None else derive_title(redacted_prompt)
    workflow_type = body.workflow_type or DEFAULT_WORKFLOW_TYPE
    user_set = [name for name, given in (("title", title), ("workflow_type", body.workflow_type)) if given]
    task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        conversation_id=conversation_id,
        title=shown_title or "Task",
        prompt=redacted_prompt,
        workflow_type=workflow_type,
        priority=body.priority or DEFAULT_PRIORITY,
        budget_limit=budget,
        checkpoint={"user_set": user_set},
        context_refs=refs if refs is not None else [],
        created_by=actor.user_id,
    )
    session.add(task)
    await session.flush()
    await env.audit.write(
        session,
        actor_type="user",
        actor_id=str(actor.user_id),
        action=AUDIT_ACTION,
        result="success",
        resource_type="task",
        resource_id=str(task.id),
        project_id=project.id,
        metadata={"source": source, "workflow_type": workflow_type},
        correlation_id=f"task:{task.id}",
        ip=actor.ip,
    )
    await session.refresh(task)  # server defaults and timestamps
    return task


async def create_task(
    env: ProjectEnv,
    actor: Actor,
    project_id: uuid.UUID,
    body: TaskCreate,
    *,
    defaults: BudgetDefaults,
    iteration_max: int,
) -> TaskView:
    ensure_engine_available()
    async with env.sessions() as session:
        project = await lock_project_for_task(session, actor, project_id)
        _checked_text(body)  # before any filesystem work
        refs: list[dict[str, object]] = []
        if body.context_refs:
            workspace = Workspace(repo_path(env.workspace_root, project.id))
            refs = await validate_refs(session, workspace, project.id, body.context_refs)
        task = await insert_task(
            session, env, actor, project, body, defaults=defaults, source="api", refs=refs
        )
        await session.commit()
        return await task_view(session, task, iteration_max=iteration_max, actor=actor)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_tasks(
    env: ProjectEnv,
    actor: Actor,
    project_id: uuid.UUID,
    *,
    statuses: frozenset[str] | None,
    q: str | None,
    page: int,
    page_size: int,
) -> tuple[list[TaskSummary], int]:
    """A project's tasks, newest change first; ``q`` is a literal, case-insensitive title match."""
    if page < 1 or not 1 <= page_size <= PAGE_SIZE_MAX:
        raise AppError(
            "VALIDATION_ERROR",
            "Input did not pass validation",
            http_status=422,
            details=[{"loc": ["query", "page_size"], "msg": f"page >= 1 and page_size 1-{PAGE_SIZE_MAX}"}],
        )
    async with env.sessions() as session:
        await authorize(session, actor, project_id, "task:read")
        conditions = [Task.project_id == project_id]
        if statuses is not None:
            conditions.append(Task.status.in_(sorted(statuses)))
        if q:
            conditions.append(Task.title.ilike(f"%{_escape_like(q)}%", escape="\\"))
        total = await session.scalar(select(func.count()).select_from(Task).where(*conditions)) or 0
        rows = list(
            await session.scalars(
                select(Task)
                .where(*conditions)
                .order_by(Task.updated_at.desc(), Task.created_at.desc(), Task.id)
                .limit(page_size)
                .offset((page - 1) * page_size)
            )
        )
        ids = [t.id for t in rows]
        usage = await task_usage(session, ids)
        pending = set(
            await session.scalars(
                select(Approval.task_id).where(Approval.task_id.in_(ids), Approval.status == "pending")
            )
        )
        return [
            TaskSummary(
                id=t.id,
                project_id=t.project_id,
                title=t.title,
                status=t.status,
                status_reason=t.status_reason,
                current_step=t.current_step,
                workflow_type=t.workflow_type,
                priority=t.priority,
                iteration=t.iteration,
                usage=usage.get(t.id, EMPTY_TOTALS),
                has_pending_approval=t.id in pending,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in rows
        ], total


async def get_task(env: ProjectEnv, actor: Actor, task_id: uuid.UUID, *, iteration_max: int) -> TaskView:
    """The task, or ``not_found()`` when it does not exist or its project is out of the actor's scope."""
    async with env.sessions() as session:
        task = await session.get(Task, task_id)
        if task is None:
            raise not_found()
        await authorize(session, actor, task.project_id, "task:read")
        return await task_view(session, task, iteration_max=iteration_max, actor=actor)


async def task_view(
    session: AsyncSession, task: Task, *, iteration_max: int, actor: Actor | None = None
) -> TaskView:
    """The ``Task`` of API §5 + §11 #1; ``actor`` decides ``pending_approval.can_decide`` (none: false)."""
    usage = (await task_usage(session, [task.id])).get(task.id, EMPTY_TOTALS)
    creator = await session.get(User, task.created_by)
    pending: ApprovalView | None = None
    approval = await session.scalar(
        select(Approval)
        .where(Approval.task_id == task.id, Approval.status == "pending")
        .order_by(Approval.created_at.desc(), Approval.id)
        .limit(1)
    )
    if approval is not None:
        project = await session.get(Project, task.project_id)
        if project is not None:
            permissions = permissions_for(actor, project) if actor is not None else frozenset[str]()
            pending = approvals.view_of(approval, project, permissions)
    checkpoint = task.checkpoint
    return TaskView(
        task=task,
        usage=usage,
        plan=checkpoint.get("plan"),
        clarification_questions=list(checkpoint.get("clarification_questions", [])),
        pending_approval=pending,
        latest_acceptance=None,
        last_event_seq=task.event_seq,
        error={"code": task.error_code, "message": task.error_message} if task.error_code else None,
        created_by={
            "id": task.created_by,
            "display_name": creator.display_name if creator is not None else None,
        },
        iteration_max=iteration_max,
        cancel_requested=task.cancel_requested,
        context_refs=list(task.context_refs),
    )


__all__ = [
    "DEFAULT_WORKFLOW_TYPE",
    "PROMPT_MAX",
    "TITLE_MAX",
    "TaskCreate",
    "TaskSummary",
    "TaskView",
    "clean_prompt_text",
    "create_task",
    "derive_title",
    "ensure_engine_available",
    "get_task",
    "insert_task",
    "list_tasks",
    "lock_project_for_task",
    "task_view",
]
