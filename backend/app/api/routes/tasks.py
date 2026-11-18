"""``POST /projects/{project_id}/tasks`` (API_CONTRACTS §5; spec WP-3.2 §8, §12). Thin.

A task is created ``draft`` and answered 202; nothing runs it until the workflow engine exists (WP-3.2b), so
until then the service answers 503 ``workflow_engine_unavailable``. The project door answers first (404 out of
scope, 403 without ``task:create``); the service authorises again under a row lock.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.agents.contracts import WorkflowType  # == WORKFLOW_TYPES
from app.api.deps import CurrentActor, StreamActor, dependencies, require_project_permission
from app.api.routes.approval_out import ApprovalOut
from app.api.routes.dashboard import UsageOut
from app.api.routes.projects import EnvDep, _respond
from app.core.context import REQUEST_ID_HEADER
from app.core.csrf import SESSION_COOKIE
from app.core.errors import AppError, request_id_of
from app.core.logging import get_logger
from app.models.enums import TASK_STATUSES
from app.services import task_events, tasks
from app.services.context_refs import MAX_CONTEXT_REFS, ContextRef
from app.services.project_budget import BudgetDefaults
from app.services.projects import PAGE_SIZE_MAX, Actor
from app.services.task_events import HISTORY_LIMIT_MAX, STREAM_CLOSE_GRACE_S, StreamSlot, stream_frames
from app.services.tasks import PROMPT_MAX, TITLE_MAX, TaskCreate, TaskSummary, TaskView

router = APIRouter(tags=["tasks"])
logger = get_logger(__name__)

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


_SEQ_MAX = 2**63 - 1
_DIGITS = re.compile(r"[0-9]{1,19}")


def _resume_point(last_event_id: str | None, after_seq: str | None) -> int:
    """``Last-Event-ID`` wins over ``?after_seq=`` (#29); each is an integer 0..2**63-1, else 422 here, for
    any length (no framework limit answers first). An empty header counts as absent (ruling R6)."""
    if last_event_id == "":
        last_event_id = None
    where, raw = ("header", last_event_id) if last_event_id is not None else ("query", after_seq)
    if raw is None:
        return 0
    value = int(raw) if _DIGITS.fullmatch(raw) else -1
    if not 0 <= value <= _SEQ_MAX:
        loc = ["header", "Last-Event-ID"] if where == "header" else ["query", "after_seq"]
        raise AppError(
            "VALIDATION_ERROR",
            "Input did not pass validation",
            http_status=422,
            details=[{"loc": loc, "msg": "must be an integer from 0 to 9223372036854775807"}],
        )
    return value


class TaskEventResponse(StreamingResponse):
    """The SSE response of one task stream (API §5.1); it owns the stream's slot and frame generator.

    Whatever ends the response (the stream's own end, a client gone before or during the body, ASGI 2.4
    ``ClientDisconnect``, a client that stops reading, a failure), ``__call__``'s ``finally`` frees the slot
    and closes the generator, so its hub listener leaves at once; both are idempotent (spec §13.5 #6). A
    background task would not do: Starlette skips it when the response fails. ``deadline_s`` bounds the whole
    response: the generator ends itself at its lifetime, and a client whose ``send`` never returns is cut
    ``STREAM_CLOSE_GRACE_S`` later. The body's log lines carry the request id.
    """

    def __init__(
        self,
        frames: AsyncGenerator[str, None],
        slot: StreamSlot,
        *,
        task_id: uuid.UUID,
        request_id: str,
        deadline_s: float,
    ) -> None:
        super().__init__(
            frames,
            media_type="text/event-stream; charset=utf-8",
            headers={REQUEST_ID_HEADER: request_id, "Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self._frames = frames
        self._slot = slot
        self._task_id = task_id
        self._request_id = request_id
        self._deadline_s = deadline_s

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        deadline = asyncio.timeout(self._deadline_s)
        with structlog.contextvars.bound_contextvars(request_id=self._request_id):
            try:
                async with deadline:
                    await super().__call__(scope, receive, send)
            except ClientDisconnect:  # ASGI 2.4: a client that left is how an event stream normally ends
                logger.info("task_stream_closed", task_id=str(self._task_id), reason="client_gone")
            except TimeoutError:
                if not deadline.expired():
                    raise
                logger.info("task_stream_closed", task_id=str(self._task_id), reason="send_timeout")
            finally:
                self._slot.release()
                await self._frames.aclose()


@router.get("/tasks/{task_id}/events")
async def task_event_stream(
    request: Request,
    task_id: uuid.UUID,
    actor: StreamActor,
    env: EnvDep,
    # No length limit here: _resume_point answers every bad value with one 422 shape.
    after_seq: str | None = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    resume = _resume_point(last_event_id, after_seq)
    await task_events.open_stream(env, actor, task_id)  # 404 before any byte
    deps = dependencies(request)
    slot = deps.stream_slots.acquire(actor.user_id)  # 429 before any byte
    try:
        timings = task_events.jittered_timings()
        frames = stream_frames(
            env,
            request.app.state.settings,
            deps.hub,
            session_token=request.cookies.get(SESSION_COOKIE, ""),
            actor=actor,
            task_id=task_id,
            after_seq=resume,
            timings=timings,
        )
        return TaskEventResponse(
            frames,
            slot,
            task_id=task_id,
            request_id=request_id_of(request),
            deadline_s=timings.max_stream_s + STREAM_CLOSE_GRACE_S,
        )
    except BaseException:
        slot.release()
        raise


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
