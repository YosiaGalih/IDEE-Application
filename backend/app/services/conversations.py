"""Conversations and chat messages (API_CONTRACTS §5; spec WP-3.2 §8). Framework-free.

A conversation belongs to the user who made it: anybody else, and anybody who can no longer see its project,
gets 404, the same answer as for a random id. A message may create a task in the same transaction (the chat
is the task's origin); any failure before the commit stores neither.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models.projects import Conversation, Message, Project
from app.services.context_refs import ContextRef, validate_refs
from app.services.project_budget import BudgetDefaults
from app.services.projects import (
    PAGE_SIZE_MAX,
    Actor,
    ProjectEnv,
    authorize,
    has_hidden_characters,
    not_found,
    repo_path,
)
from app.services.tasks import (
    TaskCreate,
    TaskView,
    clean_prompt_text,
    ensure_engine_available,
    insert_task,
    lock_project_for_task,
    task_view,
)
from app.workspace.fs import Workspace

CONVERSATION_TITLE_MAX: Final = 200


@dataclass(frozen=True)
class ConversationView:
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MessageView:
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    agent_id: str | None
    content: str
    context_refs: list[dict[str, Any]]
    task_id: uuid.UUID | None
    created_at: datetime


def _conversation_view(row: Conversation) -> ConversationView:
    return ConversationView(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _message_view(row: Message) -> MessageView:
    return MessageView(
        id=row.id,
        conversation_id=row.conversation_id,
        role=row.role,
        agent_id=row.agent_id,
        content=row.content,
        context_refs=list(row.context_refs),
        task_id=row.task_id,
        created_at=row.created_at,
    )


def _invalid(loc: list[str], message: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": loc, "msg": message}],
    )


def _check_page(page: int, page_size: int) -> None:
    if page < 1 or not 1 <= page_size <= PAGE_SIZE_MAX:
        raise _invalid(["query", "page_size"], f"page >= 1 and page_size 1-{PAGE_SIZE_MAX}")


def _require_active(project: Project) -> None:
    if project.status == "archived":
        raise AppError(
            "CONFLICT", "The project is archived", http_status=409, details=[{"reason": "project_archived"}]
        )
    if project.status != "active":
        raise AppError(
            "CONFLICT",
            "The project is not active",
            http_status=409,
            details=[{"reason": "project_not_active"}],
        )


async def _own_conversation(session: AsyncSession, actor: Actor, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != actor.user_id:
        raise not_found()
    return conversation


async def create_conversation(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, title: str | None
) -> ConversationView:
    shown = ""
    if title is not None:
        cleaned = title.strip()
        if len(cleaned) > CONVERSATION_TITLE_MAX or has_hidden_characters(cleaned):
            raise _invalid(
                ["body", "title"], f"must be one plain line of at most {CONVERSATION_TITLE_MAX} characters"
            )
        shown = env.audit.redactor.redact(cleaned)[:CONVERSATION_TITLE_MAX]
    async with env.sessions() as session:
        await authorize(session, actor, project_id, "task:create")
        row = Conversation(id=uuid.uuid4(), project_id=project_id, user_id=actor.user_id, title=shown)
        session.add(row)
        await session.flush()
        await session.refresh(row)
        await session.commit()
        return _conversation_view(row)


async def list_conversations(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, page: int, page_size: int
) -> tuple[list[ConversationView], int]:
    _check_page(page, page_size)
    async with env.sessions() as session:
        await authorize(session, actor, project_id, "project:read")
        where = (Conversation.project_id == project_id, Conversation.user_id == actor.user_id)
        total = await session.scalar(select(func.count()).select_from(Conversation).where(*where)) or 0
        rows = await session.scalars(
            select(Conversation)
            .where(*where)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return [_conversation_view(r) for r in rows], total


async def list_messages(
    env: ProjectEnv, actor: Actor, conversation_id: uuid.UUID, *, page: int, page_size: int
) -> tuple[list[MessageView], int]:
    _check_page(page, page_size)
    async with env.sessions() as session:
        conversation = await _own_conversation(session, actor, conversation_id)
        await authorize(session, actor, conversation.project_id, "project:read")
        where = Message.conversation_id == conversation_id
        total = await session.scalar(select(func.count()).select_from(Message).where(where)) or 0
        rows = await session.scalars(
            select(Message)
            .where(where)
            .order_by(Message.created_at, Message.id)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return [_message_view(r) for r in rows], total


async def post_message(
    env: ProjectEnv,
    actor: Actor,
    conversation_id: uuid.UUID,
    *,
    content: str,
    context_refs: Sequence[ContextRef],
    create_task: bool,
    workflow_type: str | None,
    budget: object | None,
    defaults: BudgetDefaults,
    iteration_max: int,
) -> tuple[MessageView, TaskView | None]:
    """The user's message and, with ``create_task``, the ``draft`` task it asks for: one transaction."""
    if create_task:
        ensure_engine_available()
    async with env.sessions() as session:
        conversation = await _own_conversation(session, actor, conversation_id)
        if create_task:
            project = await lock_project_for_task(session, actor, conversation.project_id)
        else:
            project, _ = await authorize(session, actor, conversation.project_id, "task:create")
            _require_active(project)
        cleaned = clean_prompt_text(content, field="content")  # before any filesystem work
        refs: list[dict[str, object]] = []
        if context_refs:
            workspace = Workspace(repo_path(env.workspace_root, project.id))
            refs = await validate_refs(session, workspace, project.id, context_refs)
        text = env.audit.redactor.redact(cleaned)
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            project_id=project.id,
            user_id=actor.user_id,
            role="user",
            content=text,
            context_refs=refs,
        )
        session.add(message)
        await session.flush()
        task = None
        if create_task:
            task = await insert_task(
                session,
                env,
                actor,
                project,
                TaskCreate(
                    prompt=cleaned,
                    title=None,
                    workflow_type=workflow_type,
                    priority=None,
                    budget=budget,
                    context_refs=context_refs,
                ),
                defaults=defaults,
                source="chat",
                conversation_id=conversation.id,
                refs=refs,
            )
            message.task_id = task.id
        conversation.updated_at = func.now()
        await session.flush()
        await session.refresh(message)
        await session.commit()
        view = _message_view(message)
        return view, (
            await task_view(session, task, iteration_max=iteration_max, actor=actor)
            if task is not None
            else None
        )


__all__ = [
    "ConversationView",
    "MessageView",
    "create_conversation",
    "list_conversations",
    "list_messages",
    "post_message",
]
