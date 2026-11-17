"""Conversations and chat messages (API_CONTRACTS §5; spec WP-3.2 §8). Framework-free.

A conversation belongs to the user who made it: anybody else, and anybody who can no longer see its project,
gets 404, the same answer as for a random id. Starting one, or posting to one, is a write: the project must be
``active`` (Rulings R1, R8).

A message may create a task in the same transaction (the chat is the task's origin), in the same shape as
``tasks.create_task``: a short checking session, the ref paths on disk with no session open, then one
transaction that checks again (locking the project when a task is created), stores the message and the task,
builds both views and commits. Any failure before the commit stores neither.
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
from app.services.context_refs import ContextRef, check_task_refs, has_path_refs
from app.services.project_budget import BudgetDefaults
from app.services.projects import (
    Actor,
    ProjectEnv,
    authorize,
    check_page,
    has_hidden_characters,
    not_found,
)
from app.services.tasks import (
    TaskCreate,
    TaskView,
    check_ref_paths,
    check_task,
    clean_prompt_text,
    ensure_engine_available,
    insert_task,
    project_for_task,
    require_active,
    require_repository_ready,
    task_view,
)

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


def _invalid(loc: list[str], message: str, **extra: str) -> AppError:
    return AppError(
        "VALIDATION_ERROR",
        "Input did not pass validation",
        http_status=422,
        details=[{"loc": loc, "msg": message, **extra}],
    )


def _only_with_task(*, workflow_type: str | None, budget: object | None) -> None:
    """Ruling R6: both shape the task; without one they would be dropped silently (a cost cap fails open)."""
    for field, value in (("workflow_type", workflow_type), ("budget", budget)):
        if value is not None:
            raise _invalid(["body", field], "only with create_task", reason="only_with_task")


async def _own_conversation(session: AsyncSession, actor: Actor, conversation_id: uuid.UUID) -> Conversation:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != actor.user_id:
        raise not_found()
    return conversation


async def _message_target(
    session: AsyncSession,
    actor: Actor,
    conversation_id: uuid.UUID,
    *,
    create_task: bool,
    path_refs: bool,
    lock: bool,
) -> tuple[Conversation, Project]:
    """The actor's conversation and its project, when a message can be posted now: ownership first (404),
    ``task:create`` and an active project. A message that creates a task also needs the repository ready
    (``project_for_task``, locked with ``lock``); one with file or folder refs needs it too (Ruling R5)."""
    conversation = await _own_conversation(session, actor, conversation_id)
    if create_task:
        project = await project_for_task(session, actor, conversation.project_id, lock=lock)
    else:
        project, _ = await authorize(session, actor, conversation.project_id, "task:create")
        require_active(project)
        if path_refs:
            await require_repository_ready(session, project.id)
    return conversation, project


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
        project, _ = await authorize(session, actor, project_id, "task:create")
        require_active(project)
        row = Conversation(id=uuid.uuid4(), project_id=project_id, user_id=actor.user_id, title=shown)
        session.add(row)
        await session.flush()
        await session.refresh(row)
        view = _conversation_view(row)
        await session.commit()
    return view


async def list_conversations(
    env: ProjectEnv, actor: Actor, project_id: uuid.UUID, *, page: int, page_size: int
) -> tuple[list[ConversationView], int]:
    check_page(page, page_size)
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
    check_page(page, page_size)
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
    path_refs = has_path_refs(context_refs)
    async with env.sessions() as session:
        _, project = await _message_target(
            session, actor, conversation_id, create_task=create_task, path_refs=path_refs, lock=False
        )
        project_id = project.id
    if not create_task:
        _only_with_task(workflow_type=workflow_type, budget=budget)
    cleaned = clean_prompt_text(content, field="content")
    checked = None
    if create_task:
        request = TaskCreate(
            prompt=cleaned,
            title=None,
            workflow_type=workflow_type,
            priority=None,
            budget=budget,
            context_refs=context_refs,
        )
        checked = check_task(request)
    paths = await check_ref_paths(env, project_id, context_refs)
    async with env.sessions() as session:
        conversation, project = await _message_target(
            session, actor, conversation_id, create_task=create_task, path_refs=path_refs, lock=create_task
        )
        refs = await check_task_refs(session, project.id, paths)
        message = Message(
            id=uuid.uuid4(),
            conversation_id=conversation.id,
            project_id=project.id,
            user_id=actor.user_id,
            role="user",
            content=env.audit.redactor.redact(cleaned),
            context_refs=refs,
        )
        session.add(message)
        await session.flush()
        task_out = None
        if checked is not None:
            task = await insert_task(
                session,
                env,
                actor,
                project,
                checked,
                defaults=defaults,
                source="chat",
                refs=refs,
                conversation_id=conversation.id,
            )
            message.task_id = task.id
            task_out = await task_view(session, task, iteration_max=iteration_max, actor=actor)
        conversation.updated_at = func.now()
        await session.flush()
        await session.refresh(message)
        message_out = _message_view(message)
        await session.commit()
    return message_out, task_out


__all__ = [
    "ConversationView",
    "MessageView",
    "create_conversation",
    "list_conversations",
    "list_messages",
    "post_message",
]
