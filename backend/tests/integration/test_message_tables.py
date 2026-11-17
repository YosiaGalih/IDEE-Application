"""``messages`` and ``tasks.context_refs`` (migration 0012; WP-3.2a): the role CHECK, the FK actions, the
empty-list defaults, and the model matching the migration."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from app.core.security import hash_password
from app.models.identity import User
from app.models.projects import Conversation, Message, Project
from app.models.registry import Base
from app.models.tasks import Task
from tests.conftest import alembic_config, reset_data
from tests.integration.test_task_schema import make_project, make_task


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


async def make_conversation(session: AsyncSession, project: Project) -> Conversation:
    conversation = Conversation(project_id=project.id, user_id=project.owner_id, title="c")
    session.add(conversation)
    await session.flush()
    return conversation


def message(conversation: Conversation, **overrides: object) -> Message:
    values: dict[str, object] = {
        "conversation_id": conversation.id,
        "project_id": conversation.project_id,
        "user_id": conversation.user_id,
        "role": "user",
        "content": "hello",
    }
    values.update(overrides)
    return Message(**values)


async def _count(session: AsyncSession) -> int:
    return len((await session.scalars(select(Message.id))).all())


async def test_role_is_checked(session: AsyncSession) -> None:
    project = await make_project(session)
    conversation = await make_conversation(session, project)
    session.add(message(conversation, role="bot"))
    with pytest.raises(IntegrityError, match="ck_messages_role"):
        await session.flush()


async def test_every_role_is_accepted(session: AsyncSession) -> None:
    project = await make_project(session)
    conversation = await make_conversation(session, project)
    for role in ("user", "assistant", "system", "agent"):
        session.add(message(conversation, role=role))
    await session.flush()
    assert await _count(session) == 4


async def test_messages_go_with_their_conversation(session: AsyncSession) -> None:
    project = await make_project(session)
    conversation = await make_conversation(session, project)
    other = await make_conversation(session, project)
    session.add(message(conversation))
    session.add(message(other))
    await session.flush()
    await session.execute(delete(Conversation).where(Conversation.id == conversation.id))
    assert await _count(session) == 1


async def test_messages_go_with_their_project(session: AsyncSession) -> None:
    project = await make_project(session)
    other = await make_project(session, slug="other")
    session.add(message(await make_conversation(session, project)))
    session.add(message(await make_conversation(session, other)))
    await session.flush()
    await session.execute(delete(Project).where(Project.id == project.id))
    assert await _count(session) == 1


async def test_message_outlives_its_task(session: AsyncSession) -> None:
    project = await make_project(session)
    conversation = await make_conversation(session, project)
    task = await make_task(session, project)
    row = message(conversation, task_id=task.id)
    session.add(row)
    await session.flush()
    await session.execute(delete(Task).where(Task.id == task.id))
    assert await session.scalar(text("SELECT count(*) FROM messages WHERE task_id IS NULL")) == 1


async def test_message_outlives_its_user(session: AsyncSession) -> None:
    project = await make_project(session)
    conversation = await make_conversation(session, project)
    writer = User(email="writer@example.test", display_name="W", password_hash=hash_password("x" * 12))
    session.add(writer)
    await session.flush()
    session.add(message(conversation, user_id=writer.id))
    await session.flush()
    await session.execute(delete(User).where(User.id == writer.id))
    assert await session.scalar(text("SELECT count(*) FROM messages WHERE user_id IS NULL")) == 1
    assert await _count(session) == 1


async def test_context_refs_default_to_an_empty_list(session: AsyncSession) -> None:
    project = await make_project(session)
    conversation = await make_conversation(session, project)
    task = await make_task(session, project)
    row = message(conversation)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    await session.refresh(task)
    assert row.context_refs == []
    assert task.context_refs == []
    assert row.created_at is not None
    assert isinstance(row.id, uuid.UUID)


# --- migration ---------------------------------------------------------------------------------------------


def _table_diff(connection: Connection) -> list[object]:
    tables = {"messages", "tasks"}

    def touches(change: object) -> bool:
        if isinstance(change, list | tuple):
            return any(touches(item) for item in change)  # pyright: ignore[reportUnknownVariableType]
        if isinstance(change, str):
            return change in tables
        table = getattr(change, "table", None)
        return getattr(change, "name", None) in tables or getattr(table, "name", None) in tables

    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "compare_server_default": True}
    )
    return [change for change in compare_metadata(context, Base.metadata) if touches(change)]


async def test_models_match_the_migration(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        assert await connection.run_sync(_table_diff) == []
        columns = {
            row[0]: (row[1], row[2])
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
                        "WHERE table_name = 'messages'"
                    )
                )
            ).all()
        }
        indexes = {
            row[0]: row[1]
            for row in (
                await connection.execute(
                    text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'messages'")
                )
            ).all()
        }
    assert columns == {
        "id": ("NO", "uuid"),
        "conversation_id": ("NO", "uuid"),
        "project_id": ("NO", "uuid"),
        "user_id": ("YES", "uuid"),
        "role": ("NO", "character varying"),
        "agent_id": ("YES", "character varying"),
        "content": ("NO", "text"),
        "context_refs": ("NO", "jsonb"),
        "task_id": ("YES", "uuid"),
        "created_at": ("NO", "timestamp with time zone"),
    }
    assert "(conversation_id, created_at, id)" in indexes["ix_messages_conversation_id_created_at"]
    assert "(task_id)" in indexes["ix_messages_task_id"]


async def _schema_state(database_url: str) -> tuple[bool, bool]:
    """(messages exists, tasks.context_refs exists)."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(text("SELECT to_regclass('public.messages') IS NOT NULL"))
            column = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'tasks' AND column_name = 'context_refs'"
                )
            )
    finally:
        await engine.dispose()
    return bool(exists), column == 1


def test_migration_round_trip(migrated_database: str) -> None:
    config = alembic_config(migrated_database)
    assert asyncio.run(_schema_state(migrated_database)) == (True, True)
    command.downgrade(config, "0011_agent_run_error_code")
    assert asyncio.run(_schema_state(migrated_database)) == (False, False)
    command.upgrade(config, "head")
    assert asyncio.run(_schema_state(migrated_database)) == (True, True)
