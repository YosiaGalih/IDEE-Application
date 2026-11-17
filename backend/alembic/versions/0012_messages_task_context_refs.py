"""messages (DATA_MODEL M07) and tasks.context_refs: the chat history of a conversation and the references a
task was created with (WP-3.2a).

Revision ID: 0012_messages_task_context_refs
Revises: 0011_agent_run_error_code
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_messages_task_context_refs"
down_revision: str | Sequence[str] | None = "0011_agent_run_error_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied, not imported: a migration keeps the values it was written with (app.models.enums may move on).
MESSAGE_ROLES = ("user", "assistant", "system", "agent")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context_refs", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_in("role", MESSAGE_ROLES), name=op.f("ck_messages_role")),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_messages_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_messages_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_messages_task_id_tasks"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(
        "ix_messages_conversation_id_created_at", "messages", ["conversation_id", "created_at", "id"]
    )
    op.create_index("ix_messages_task_id", "messages", ["task_id"])
    # FK scans: deleting a project cascades to its messages, deleting a user sets theirs to null (Ruling R9).
    op.create_index("ix_messages_project_id", "messages", ["project_id"])
    op.create_index("ix_messages_user_id", "messages", ["user_id"])
    op.add_column(
        "tasks",
        sa.Column("context_refs", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("tasks", "context_refs")
    op.drop_index("ix_messages_user_id", table_name="messages")
    op.drop_index("ix_messages_project_id", table_name="messages")
    op.drop_index("ix_messages_task_id", table_name="messages")
    op.drop_index("ix_messages_conversation_id_created_at", table_name="messages")
    op.drop_table("messages")
