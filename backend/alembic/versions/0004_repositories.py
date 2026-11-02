"""repositories: one git repository per project (DATA_MODEL M04, WP-2.1a)

Revision ID: 0004_repositories
Revises: 0003_tasks
Create Date: 2026-11-02 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_repositories"
down_revision: str | Sequence[str] | None = "0003_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repositories",
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("remote_url", sa.String(length=500), nullable=True),
        sa.Column("branch", sa.String(length=100), nullable=False),
        sa.Column("credential_ref", sa.String(length=200), nullable=True),
        sa.Column("sync_state", sa.String(length=16), server_default=sa.text("'ready'"), nullable=False),
        sa.Column("index_commit", sa.String(length=64), nullable=True),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "provider IN ('local', 'template', 'upload', 'git_https')", name=op.f("ck_repositories_provider")
        ),
        sa.CheckConstraint(
            "sync_state IN ('ready', 'importing', 'indexing', 'error')", name=op.f("ck_repositories_sync_state")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_repositories_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repositories")),
        sa.UniqueConstraint("project_id", name=op.f("uq_repositories_project_id")),
    )


def downgrade() -> None:
    op.drop_table("repositories")
