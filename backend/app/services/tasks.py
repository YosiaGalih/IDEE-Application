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
from typing import Any, Final, Literal

from sqlalchemy import select
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
    Actor,
    ProjectEnv,
    authorize,
    has_hidden_characters,
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


def _checked_text(body: TaskCreate) -> tuple[str, str | None]:
    """(prompt, title) as the user sent them, or a 422: the prompt is non-blank text (line breaks and tabs
    allowed), the title one plain line."""
    if not body.prompt.strip() or len(body.prompt) > PROMPT_MAX:
        raise _invalid("prompt", f"must be 1-{PROMPT_MAX} characters")
    if has_hidden_characters(body.prompt.replace("\n", "").replace("\t", "")):
        raise _invalid("prompt", "must be plain text")
    title = body.title.strip() if body.title is not None else None
    if title is not None and (not 1 <= len(title) <= TITLE_MAX or has_hidden_characters(title)):
        raise _invalid("title", f"must be one plain line of 1-{TITLE_MAX} characters")
    return body.prompt, title


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
    "TaskView",
    "create_task",
    "derive_title",
    "ensure_engine_available",
    "insert_task",
    "lock_project_for_task",
    "task_view",
]
