"""Conversations and chat messages (API_CONTRACTS §5; spec WP-3.2 §8). Thin.

``POST /conversations/{id}/messages``: 202 ``accepted`` when the message created a task, 201 ``completed``
with ``task: null`` otherwise. A conversation is its creator's: anybody else gets 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agents.contracts import WorkflowType
from app.api.deps import CurrentActor, require_project_permission
from app.api.routes.projects import EnvDep, _respond
from app.api.routes.tasks import TaskOut
from app.services import conversations
from app.services.context_refs import MAX_CONTEXT_REFS, ContextRef
from app.services.conversations import CONVERSATION_TITLE_MAX, ConversationView, MessageView
from app.services.project_budget import BudgetDefaults
from app.services.projects import PAGE_SIZE_MAX, Actor
from app.services.tasks import PROMPT_MAX

router = APIRouter(tags=["conversations"])

ProjectReader = Annotated[Actor, Depends(require_project_permission("project:read"))]
TaskCreator = Annotated[Actor, Depends(require_project_permission("task:create"))]


class ConversationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str | None, Field(max_length=2 * CONVERSATION_TITLE_MAX)] = None


class MessageCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Raw cap against abuse only; the service measures the cleaned text.
    content: Annotated[str, Field(max_length=2 * PROMPT_MAX)]
    context_refs: Annotated[list[ContextRef], Field(max_length=MAX_CONTEXT_REFS)] = []
    create_task: bool = True
    workflow_type: WorkflowType | None = None
    budget: dict[str, Any] | None = None


class ConversationOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, view: ConversationView) -> ConversationOut:
        return cls(
            id=view.id,
            project_id=view.project_id,
            title=view.title,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    agent_id: str | None
    content: str
    context_refs: list[dict[str, Any]]
    task_id: uuid.UUID | None
    created_at: datetime

    @classmethod
    def of(cls, view: MessageView) -> MessageOut:
        return cls(
            id=view.id,
            conversation_id=view.conversation_id,
            role=view.role,
            agent_id=view.agent_id,
            content=view.content,
            context_refs=view.context_refs,
            task_id=view.task_id,
            created_at=view.created_at,
        )


@router.get("/projects/{project_id}/conversations")
async def list_conversations(
    request: Request,
    project_id: uuid.UUID,
    actor: ProjectReader,
    env: EnvDep,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 50,
) -> JSONResponse:
    rows, total = await conversations.list_conversations(
        env, actor, project_id, page=page, page_size=page_size
    )
    return _respond(
        request,
        [ConversationOut.of(r).model_dump(mode="json") for r in rows],
        meta={"total": total, "page": page, "page_size": page_size},
    )


@router.post("/projects/{project_id}/conversations")
async def create_conversation(
    request: Request, project_id: uuid.UUID, body: ConversationCreateIn, actor: TaskCreator, env: EnvDep
) -> JSONResponse:
    view = await conversations.create_conversation(env, actor, project_id, body.title)
    return _respond(request, ConversationOut.of(view).model_dump(mode="json"), status_code=201)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    request: Request,
    conversation_id: uuid.UUID,
    actor: CurrentActor,
    env: EnvDep,
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = 50,
) -> JSONResponse:
    rows, total = await conversations.list_messages(
        env, actor, conversation_id, page=page, page_size=page_size
    )
    return _respond(
        request,
        [MessageOut.of(r).model_dump(mode="json") for r in rows],
        meta={"total": total, "page": page, "page_size": page_size},
    )


@router.post("/conversations/{conversation_id}/messages")
async def post_message(
    request: Request, conversation_id: uuid.UUID, body: MessageCreateIn, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    settings = request.app.state.settings
    message, task = await conversations.post_message(
        env,
        actor,
        conversation_id,
        content=body.content,
        context_refs=body.context_refs,
        create_task=body.create_task,
        workflow_type=body.workflow_type,
        budget=body.budget,
        defaults=BudgetDefaults.from_settings(settings),
        iteration_max=settings.task_max_iterations,
    )
    data = {
        "message": MessageOut.of(message).model_dump(mode="json"),
        "task": TaskOut.of(task).model_dump(mode="json") if task is not None else None,
    }
    if task is None:
        return _respond(request, data, status_code=201)
    return _respond(request, data, status_code=202, status="accepted")
