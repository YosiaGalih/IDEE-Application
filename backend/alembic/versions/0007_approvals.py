"""approvals + projects.deleted_at (WP-3.1c1: DATA_MODEL approvals, ADR-024 handler columns, ADR-034).

Revision ID: 0007_approvals
Revises: 0006_agents
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_approvals"
down_revision: str | Sequence[str] | None = "0006_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTION_TYPES = (
    "apply_changes",
    "budget_increase",
    "tool_r3",
    "project_delete",
    "deploy_staging",
    "deploy_production",
    "git_push",
    "waive_criterion",
)
STATUSES = ("pending", "approved", "rejected", "expired", "consumed", "superseded")
HANDLER_STATUSES = ("pending", "succeeded", "failed", "not_required")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("projects", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    # No code path set "deleted" before this revision; a row edited by hand gets its purge clock started.
    op.execute("UPDATE projects SET deleted_at = updated_at WHERE status = 'deleted'")
    op.create_check_constraint(
        op.f("ck_projects_deleted_at"), "projects", "(status = 'deleted') = (deleted_at IS NOT NULL)"
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.SmallInteger(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "handler_status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("handler_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("handler_error", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("action_type", ACTION_TYPES), name=op.f("ck_approvals_action_type")),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_approvals_status")),
        sa.CheckConstraint(_in("handler_status", HANDLER_STATUSES), name=op.f("ck_approvals_handler_status")),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_approvals_payload_hash")),
        sa.CheckConstraint(
            "requested_by ~ '^(user:[0-9a-f-]{36}|system:[a-z_]{1,40})$'",
            name=op.f("ck_approvals_requested_by"),
        ),
        sa.CheckConstraint("risk_level BETWEEN 0 AND 4", name=op.f("ck_approvals_risk_level")),
        sa.CheckConstraint("char_length(decision_note) <= 2000", name=op.f("ck_approvals_decision_note")),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_approvals_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_approvals_task_id_tasks"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_approvals_decided_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    op.create_index("ix_approvals_project_id_status", "approvals", ["project_id", "status"])
    op.create_index(
        "ix_approvals_handler_due",
        "approvals",
        ["handler_status"],
        postgresql_where=sa.text(
            "handler_status IN ('pending', 'failed') AND status IN ('approved', 'rejected', 'expired')"
        ),
    )
    op.create_index(
        "uq_approvals_open_project_delete",
        "approvals",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("action_type = 'project_delete' AND status IN ('pending', 'approved')"),
    )


def downgrade() -> None:
    op.drop_index("uq_approvals_open_project_delete", table_name="approvals")
    op.drop_index("ix_approvals_handler_due", table_name="approvals")
    op.drop_index("ix_approvals_project_id_status", table_name="approvals")
    op.drop_table("approvals")
    op.drop_constraint(op.f("ck_projects_deleted_at"), "projects", type_="check")
    op.drop_column("projects", "deleted_at")
