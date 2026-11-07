"""tool_calls + approvals status guard + one open tool_r3 per task (WP-3.1d1: DATA_MODEL tool_calls, spec
WP-3.1d Goals 4 to 6).

Revision ID: 0008_tool_calls
Revises: 0007_approvals
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_tool_calls"
down_revision: str | Sequence[str] | None = "0007_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("running", "ok", "error", "denied", "timeout", "approval_required", "cancelled")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("agent_run_id", sa.UUID(), nullable=True),
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("tool_use_id", sa.String(length=128), nullable=True),
        sa.Column("risk_level", sa.SmallInteger(), nullable=False),
        sa.Column("idempotency_key", sa.CHAR(length=64), nullable=True),
        sa.Column("input_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("sanitized_input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("output_ref", sa.UUID(), nullable=True),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_tool_calls_status")),
        sa.CheckConstraint("risk_level BETWEEN 0 AND 4", name=op.f("ck_tool_calls_risk_level")),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_tool_calls_input_hash")),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR idempotency_key ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_tool_calls_idempotency_key"),
        ),
        sa.CheckConstraint("char_length(output_summary) <= 2048", name=op.f("ck_tool_calls_output_summary")),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name=op.f("ck_tool_calls_duration_ms")),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_tool_calls_task_id_tasks"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_tool_calls_agent_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approvals.id"],
            name=op.f("fk_tool_calls_approval_id_approvals"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_tool_calls_idempotency_key")),
    )
    op.create_index("ix_tool_calls_task_id_created_at", "tool_calls", ["task_id", "created_at"])
    op.create_index("ix_tool_calls_agent_run_id_tool_name", "tool_calls", ["agent_run_id", "tool_name"])
    op.create_index(
        "uq_approvals_open_tool_r3",
        "approvals",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("action_type = 'tool_r3' AND status IN ('pending', 'approved')"),
    )
    # CLAUDE.md #5 backstop in the database (3.1c1 AG-13 C3): an approval starts pending, and its status only
    # changes through app.services.approval_store, which sets the transaction-local kk.approval_status_writer
    # marker, and only along the approval cycle. What was approved (action, target, payload, hash, requester)
    # never changes after the insert, whoever writes (AG-13 3.1d1). The marker also lets a test insert a
    # decided row.
    op.execute(
        """
        CREATE FUNCTION approvals_status_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            writer text := coalesce(current_setting('kk.approval_status_writer', true), '');
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'pending' AND writer <> 'approval_store' THEN
                    RAISE EXCEPTION 'approvals start as pending'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                RETURN NEW;
            END IF;
            IF (NEW.project_id, NEW.task_id, NEW.action_type, NEW.risk_level, NEW.payload, NEW.payload_hash,
                NEW.requested_by, NEW.created_at)
               IS DISTINCT FROM
               (OLD.project_id, OLD.task_id, OLD.action_type, OLD.risk_level, OLD.payload, OLD.payload_hash,
                OLD.requested_by, OLD.created_at) THEN
                RAISE EXCEPTION 'what an approval is for never changes after it is requested'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                IF writer <> 'approval_store' THEN
                    RAISE EXCEPTION 'approvals.status may only change through approval_store'
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
                IF NOT (
                    (OLD.status = 'pending'
                        AND NEW.status IN ('approved', 'rejected', 'expired', 'superseded'))
                    OR (OLD.status = 'approved' AND NEW.status IN ('consumed', 'expired', 'superseded'))
                ) THEN
                    RAISE EXCEPTION 'an approval cannot move from % to %', OLD.status, NEW.status
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER approvals_status_guard BEFORE INSERT OR UPDATE ON approvals "
        "FOR EACH ROW EXECUTE FUNCTION approvals_status_guard()"
    )
    op.execute("ALTER TABLE approvals ENABLE ALWAYS TRIGGER approvals_status_guard")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS approvals_status_guard ON approvals")
    op.execute("DROP FUNCTION IF EXISTS approvals_status_guard()")
    op.drop_index("uq_approvals_open_tool_r3", table_name="approvals")
    op.drop_index("ix_tool_calls_agent_run_id_tool_name", table_name="tool_calls")
    op.drop_index("ix_tool_calls_task_id_created_at", table_name="tool_calls")
    op.drop_table("tool_calls")
