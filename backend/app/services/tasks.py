"""Tasks: create (API_CONTRACTS §5; spec WP-3.2 §8, §12) and the ``Task`` view. Framework-free.

**Gate.** Until WP-3.2d registers the first production steps and flips
``RUNTIMES_AVAILABLE["workflow_engine"]``, a task cannot be created: nothing could ever run it. The flag means
"the code exists", not a health check. The gate is read through the module attribute, so a test can
open it. A task is created ``draft``; no job is enqueued here.

**Two short transactions** (Ruling R11). Creating a task:
1. checks scope and state in a short session without a lock (404/403, then 409);
2. checks the input (422), then the context-ref paths on disk, with no session open: a slow disk never holds a
   pooled connection or the project row;
3. locks the project and checks it again, checks task refs, inserts the row and its audit event, builds the
   view and only then commits. A failure anywhere before the commit stores nothing, so a retry with the same
   idempotency key (released on a 5xx) cannot create a second task.

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
from app.core.logging import get_logger
from app.models.approvals import Approval
from app.models.identity import User
from app.models.projects import Project, Repository
from app.models.tasks import Task
from app.services import approvals
from app.services.approvals import ApprovalView
from app.services.context_refs import ContextRef, RefCheck, check_paths, check_task_refs
from app.services.project_budget import BUDGET_SETTINGS_KEY
from app.services.projects import (
    Actor,
    ProjectEnv,
    authorize,
    check_page,
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

logger = get_logger(__name__)

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
class CheckedTask:
    """A ``TaskCreate`` past every check that needs no IO: the text cleaned, the requested budget parsed."""

    prompt: str
    title: str | None
    workflow_type: str | None
    priority: str | None
    budget: BudgetFields


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


def require_active(project: Project) -> None:
    """409 unless the project takes writes now: ``project_archived``, else ``project_not_active``."""
    if project.status == "archived":
        raise _conflict("project_archived", "The project is archived")
    if project.status != "active":
        raise _conflict("project_not_active", "The project is not active")


async def require_repository_ready(session: AsyncSession, project_id: uuid.UUID) -> None:
    sync_state = await session.scalar(
        select(Repository.sync_state).where(Repository.project_id == project_id)
    )
    if sync_state != "ready":
        raise _conflict("repository_not_ready", "The repository is not ready")


async def project_for_task(
    session: AsyncSession, actor: Actor, project_id: uuid.UUID, *, lock: bool
) -> Project:
    """The project when it can take a task now: ``task:create``, active, repository ready. With ``lock`` the
    row is held (``FOR NO KEY UPDATE``) until the transaction ends: the inserting session takes it, the first
    short session does not."""
    project, _ = await authorize(session, actor, project_id, "task:create", lock=lock)
    require_active(project)
    await require_repository_ready(session, project.id)
    return project


async def check_ref_paths(env: ProjectEnv, project_id: uuid.UUID, refs: Sequence[ContextRef]) -> RefCheck:
    """``check_paths`` against the project's ``repo/``. Call it with no session open (R11)."""
    return await check_paths(Workspace(repo_path(env.workspace_root, project_id)), project_id, refs)


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


def check_task(body: TaskCreate) -> CheckedTask:
    """The prompt through ``clean_prompt_text``, the title one plain line, the requested budget parsed: every
    422 that needs no IO, so it comes before any filesystem work."""
    prompt = clean_prompt_text(body.prompt, field="prompt")
    title = body.title.strip() if body.title is not None else None
    if title is not None and (not 1 <= len(title) <= TITLE_MAX or has_hidden_characters(title)):
        raise _invalid("title", f"must be one plain line of 1-{TITLE_MAX} characters")
    try:
        budget = parse_budget(body.budget)
    except BudgetConfigError as exc:
        raise _invalid("budget", str(exc)) from None
    return CheckedTask(prompt, title, body.workflow_type, body.priority, budget)


async def insert_task(
    session: AsyncSession,
    env: ProjectEnv,
    actor: Actor,
    project: Project,
    checked: CheckedTask,
    *,
    defaults: BudgetDefaults,
    source: Source,
    refs: list[dict[str, object]],
    conversation_id: uuid.UUID | None = None,
) -> Task:
    """The ``draft`` row and its audit event, in the caller's transaction (no commit). ``refs`` are the
    caller's checked context refs (chat checks them once for the message and the task)."""
    try:
        project_fields = parse_budget(project.settings.get(BUDGET_SETTINGS_KEY))
    except BudgetConfigError:
        logger.error("project_budget_invalid", project_id=str(project.id))  # never the settings content
        raise _conflict("project_budget_invalid", "The project's stored budget cannot be read") from None
    effective = effective_budget(checked.budget, project_fields, defaults)
    budget = dump_budget(BudgetFields(**asdict(effective)))

    redactor = env.audit.redactor
    redacted_prompt = redactor.redact(checked.prompt)
    # Never empty: a checked title has a visible character, and so has the first line of a checked prompt.
    title = (
        redactor.redact(checked.title)[:TITLE_MAX]
        if checked.title is not None
        else derive_title(redacted_prompt)
    )
    workflow_type = checked.workflow_type or DEFAULT_WORKFLOW_TYPE
    user_set = [
        name for name, given in (("title", checked.title), ("workflow_type", checked.workflow_type)) if given
    ]
    task = Task(
        id=uuid.uuid4(),
        project_id=project.id,
        conversation_id=conversation_id,
        title=title,
        prompt=redacted_prompt,
        workflow_type=workflow_type,
        priority=checked.priority or DEFAULT_PRIORITY,
        budget_limit=budget,
        checkpoint={"user_set": user_set},
        context_refs=refs,
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
        metadata={"task_id": str(task.id), "source": source, "workflow_type": workflow_type},
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
        await project_for_task(session, actor, project_id, lock=False)
    checked = check_task(body)
    paths = await check_ref_paths(env, project_id, body.context_refs)
    async with env.sessions() as session:
        project = await project_for_task(session, actor, project_id, lock=True)
        refs = await check_task_refs(session, project.id, paths)
        task = await insert_task(
            session, env, actor, project, checked, defaults=defaults, source="api", refs=refs
        )
        view = await task_view(session, task, iteration_max=iteration_max, actor=actor)
        await session.commit()
    return view


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
    check_page(page, page_size)
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


async def task_view(session: AsyncSession, task: Task, *, iteration_max: int, actor: Actor) -> TaskView:
    """The ``Task`` of API §5 + §11 #1; ``actor`` decides ``pending_approval.can_decide``."""
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
            pending = approvals.view_of(approval, project, permissions_for(actor, project))
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
    "CheckedTask",
    "TaskCreate",
    "TaskSummary",
    "TaskView",
    "check_ref_paths",
    "check_task",
    "clean_prompt_text",
    "create_task",
    "derive_title",
    "ensure_engine_available",
    "get_task",
    "insert_task",
    "list_tasks",
    "project_for_task",
    "require_active",
    "require_repository_ready",
    "task_view",
]
