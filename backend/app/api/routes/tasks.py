"""``POST /projects/{project_id}/tasks`` (API_CONTRACTS §5; spec WP-3.2 §8, §12). Thin.

A task is created ``draft`` and answered 202; nothing runs it until the workflow engine exists (WP-3.2b), so
until then the service answers 503 ``workflow_engine_unavailable``. The project door answers first (404 out of
scope, 403 without ``task:create``); the service authorises again under a row lock.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agents.contracts import WorkflowType  # == WORKFLOW_TYPES
from app.api.deps import CurrentActor, require_project_permission
from app.api.routes.approval_out import ApprovalOut
from app.api.routes.dashboard import UsageOut
from app.api.routes.projects import EnvDep, _respond
from app.core.errors import AppError
from app.models.enums import TASK_STATUSES
from app.services import task_events, tasks
from app.services.context_refs import MAX_CONTEXT_REFS, ContextRef
from app.services.project_budget import BudgetDefaults
from app.services.projects import PAGE_SIZE_MAX, Actor
from app.services.task_events import HISTORY_LIMIT_MAX
from app.services.tasks import PROMPT_MAX, TITLE_MAX, TaskCreate, TaskSummary, TaskView

router = APIRouter(tags=["tasks"])

TaskCreator = Annotated[Actor, Depends(require_project_permission("task:create"))]
TaskReader = Annotated[Actor, Depends(require_project_permission("task:read"))]


class TaskCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Raw cap against abuse only; the service measures the cleaned (CRLF-normalised, stripped) text.
    prompt: Annotated[str, Field(max_length=2 * PROMPT_MAX)]
    title: Annotated[str | None, Field(max_length=TITLE_MAX)] = None
    workflow_type: WorkflowType | None = None
    priority: Literal["low", "normal", "high"] | None = None
    budget: dict[str, Any] | None = None
    context_refs: Annotated[list[ContextRef], Field(max_length=MAX_CONTEXT_REFS)] = []

    def to_service(self) -> TaskCreate:
        return TaskCreate(
            prompt=self.prompt,
            title=self.title,
            workflow_type=self.workflow_type,
            priority=self.priority,
            budget=self.budget,
            context_refs=self.context_refs,
        )


class CreatorOut(BaseModel):
    id: uuid.UUID
    display_name: str | None


class TaskErrorOut(BaseModel):
    code: str | None
    message: str | None


class TaskOut(BaseModel):
    """``Task`` (API §5) + §11 #1: ``iteration_max``, ``clarification_questions``, ``cancel_requested``,
    ``context_refs``."""

    id: uuid.UUID
    project_id: uuid.UUID
    conversation_id: uuid.UUID | None
    title: str
    prompt: str
    workflow_type: str
    status: str
    status_reason: str | None
    current_step: str | None
    priority: str
    iteration: int
    iteration_max: int
    debug_attempts: int
    budget_limit: dict[str, Any]
    usage: UsageOut
    plan: Any | None
    clarification_questions: list[Any]
    branch_name: str | None
    base_commit: str | None
    created_by: CreatorOut
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: TaskErrorOut | None
    pending_approval: ApprovalOut | None
    latest_acceptance: None
    last_event_seq: int
    cancel_requested: bool
    context_refs: list[dict[str, Any]]

    @classmethod
    def of(cls, view: TaskView) -> TaskOut:
        t = view.task
        return cls(
            id=t.id,
            project_id=t.project_id,
            conversation_id=t.conversation_id,
            title=t.title,
            prompt=t.prompt,
            workflow_type=t.workflow_type,
            status=t.status,
            status_reason=t.status_reason,
            current_step=t.current_step,
            priority=t.priority,
            iteration=t.iteration,
            iteration_max=view.iteration_max,
            debug_attempts=t.debug_attempts,
            budget_limit=dict(t.budget_limit),
            usage=UsageOut.of(view.usage),
            plan=view.plan,
            clarification_questions=view.clarification_questions,
            branch_name=t.branch_name,
            base_commit=t.base_commit,
            created_by=CreatorOut(**view.created_by),
            created_at=t.created_at,
            updated_at=t.updated_at,
            started_at=t.started_at,
            completed_at=t.completed_at,
            error=TaskErrorOut(**view.error) if view.error is not None else None,
            pending_approval=(
                ApprovalOut.of(view.pending_approval) if view.pending_approval is not None else None
            ),
            latest_acceptance=None,
            last_event_seq=view.last_event_seq,
            cancel_requested=view.cancel_requested,
            context_refs=view.context_refs,
        )


class TaskSummaryOut(BaseModel):
    """A row of the task list (§11 #2)."""

    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    status: str
    status_reason: str | None
    current_step: str | None
    workflow_type: str
    priority: str
    iteration: int
    usage: UsageOut
    has_pending_approval: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, row: TaskSummary) -> TaskSummaryOut:
        return cls(
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            status=row.status,
            status_reason=row.status_reason,
            current_step=row.current_step,
            workflow_type=row.workflow_type,
            priority=row.priority,
            iteration=row.iteration,
            usage=UsageOut.of(row.usage),
            has_pending_approval=row.has_pending_approval,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _statuses(raw: str | None) -> frozenset[str] | None:
    """``status=a,b`` -> the set; an unknown value is a 422 at ``["query", "status"]``."""
    if raw is None:
        return None
    values = [part.strip() for part in raw.split(",")]
    unknown = [v for v in values if v not in TASK_STATUSES]
    if unknown:
        raise AppError(
            "VALIDATION_ERROR",
            "Input did not pass validation",
            http_status=422,
            details=[{"loc": ["query", "status"], "msg": f"unknown status: {', '.join(unknown)[:80]}"}],
        )
    return frozenset(values)


@router.get("/projects/{project_id}/tasks")
async def list_tasks(
    request: Request,
    project_id: uuid.UUID,
    actor: TaskReader,
    env: EnvDep,
    status: Annotated[str | None, Query(max_length=400)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 50,
) -> JSONResponse:
    rows, total = await tasks.list_tasks(
        env, actor, project_id, statuses=_statuses(status), q=q, page=page, page_size=page_size
    )
    return _respond(
        request,
        [TaskSummaryOut.of(row).model_dump(mode="json") for row in rows],
        meta={"total": total, "page": page, "page_size": page_size},
    )


@router.get("/tasks/{task_id}")
async def get_task(request: Request, task_id: uuid.UUID, actor: CurrentActor, env: EnvDep) -> JSONResponse:
    view = await tasks.get_task(
        env, actor, task_id, iteration_max=request.app.state.settings.task_max_iterations
    )
    return _respond(request, TaskOut.of(view).model_dump(mode="json"))


@router.get("/tasks/{task_id}/events/history")
async def task_event_history(
    request: Request,
    task_id: uuid.UUID,
    actor: CurrentActor,
    env: EnvDep,
    after_seq: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    limit: Annotated[int, Query(ge=1, le=HISTORY_LIMIT_MAX)] = 100,
) -> JSONResponse:
    messages, next_after_seq = await task_events.history(
        env, actor, task_id, after_seq=after_seq, limit=limit
    )
    return _respond(request, messages, meta={"next_after_seq": next_after_seq})


@router.post("/projects/{project_id}/tasks")
async def create_task(
    request: Request, project_id: uuid.UUID, body: TaskCreateIn, actor: TaskCreator, env: EnvDep
) -> JSONResponse:
    settings = request.app.state.settings
    view = await tasks.create_task(
        env,
        actor,
        project_id,
        body.to_service(),
        defaults=BudgetDefaults.from_settings(settings),
        iteration_max=settings.task_max_iterations,
    )
    return _respond(request, TaskOut.of(view).model_dump(mode="json"), status_code=202, status="accepted")
