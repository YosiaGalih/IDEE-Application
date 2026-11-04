"""repo_index_entries: the files and symbols of a project's indexed commit (DATA_MODEL M04/M16, WP-2.1d)

Revision ID: 0005_repo_index_entries
Revises: 0004_repositories
Create Date: 2026-11-04 09:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_repo_index_entries"
down_revision: str | Sequence[str] | None = "0004_repositories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "repo_index_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=300), nullable=True),
        sa.Column("symbol_kind", sa.String(length=40), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=40), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("commit", sa.String(length=64), nullable=False),
        sa.CheckConstraint("kind IN ('file', 'symbol')", name=op.f("ck_repo_index_entries_kind")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_repo_index_entries_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repo_index_entries")),
    )
    op.create_index("ix_repo_index_entries_project_id_kind", "repo_index_entries", ["project_id", "kind"])
    op.create_index("ix_repo_index_entries_project_id_symbol", "repo_index_entries", ["project_id", "symbol"])


def downgrade() -> None:
    op.drop_index("ix_repo_index_entries_project_id_symbol", table_name="repo_index_entries")
    op.drop_index("ix_repo_index_entries_project_id_kind", table_name="repo_index_entries")
    op.drop_table("repo_index_entries")
