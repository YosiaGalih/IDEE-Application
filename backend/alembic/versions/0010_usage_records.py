"""usage_records, the usage ledger (WP-3.1f1: FR-133, spec WP-3.1f Keputusan Lead #3, #4), and
agent_runs.estimated_cost widened to numeric(20,10) so a run total keeps what the ledger keeps (Keputusan
Lead #12).

Revision ID: 0010_usage_records
Revises: 0009_provider_configs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_usage_records"
down_revision: str | Sequence[str] | None = "0009_provider_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTEMPT_STATUSES = ("ok", "error", "cancelled")
ERROR_KINDS = (
    "rate_limited",
    "overloaded",
    "transient",
    "timeout",
    "refusal",
    "bad_request",
    "auth",
    "malformed",
    "cancelled",
    "max_tokens",
)
COST_STATUSES = ("estimated", "partial", "actual", "unavailable")
TOKENS = ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _check(body: str, rule: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(body, name=op.f(f"ck_usage_records_{rule}"))


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=True),
        sa.Column("agent_run_id", sa.UUID(), nullable=True),
        sa.Column("agent_id", sa.String(length=40), nullable=True),
        sa.Column("call_id", sa.UUID(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("request_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_kind", sa.String(length=20), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("input_token_bound", sa.Integer(), nullable=False),
        sa.Column("output_token_cap", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column("actual_cost", sa.Numeric(precision=20, scale=10), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("cost_status", sa.String(length=16), nullable=False),
        sa.Column("pricing_version", sa.String(length=40), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        _check("attempt >= 1", "attempt"),
        _check("retry_count = attempt - 1", "retry_count"),
        _check(_in("status", ATTEMPT_STATUSES), "status"),
        _check(f"error_kind IS NULL OR {_in('error_kind', ERROR_KINDS)}", "error_kind"),
        _check("status <> 'ok' OR error_kind IS NULL", "ok_has_no_error"),
        _check("(status = 'cancelled') = (coalesce(error_kind, '') = 'cancelled')", "cancelled_kind"),
        _check(" AND ".join(f"({c} IS NULL OR {c} >= 0)" for c in TOKENS), "tokens"),
        _check(
            f"({' AND '.join(f'{c} IS NULL' for c in TOKENS)}) "
            f"OR ({' AND '.join(f'{c} IS NOT NULL' for c in TOKENS)})",
            "tokens_together",
        ),
        _check("input_token_bound >= 0", "input_token_bound"),
        _check("output_token_cap > 0", "output_token_cap"),
        _check("estimated_cost IS NULL OR estimated_cost >= 0", "estimated_cost"),
        _check("actual_cost IS NULL", "actual_cost"),
        _check("currency = 'USD'", "currency"),
        _check(_in("cost_status", COST_STATUSES), "cost_status"),
        _check("(estimated_cost IS NULL) = (cost_status = 'unavailable')", "cost_matches_status"),
        _check("status = 'ok' OR cost_status <> 'estimated'", "failed_not_estimated"),
        _check("input_tokens IS NOT NULL OR cost_status = 'unavailable'", "no_tokens_unavailable"),
        _check("duration_ms >= 0", "duration_ms"),
        _check("agent_run_id IS NULL OR (task_id IS NOT NULL AND agent_id IS NOT NULL)", "run_has_task"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_usage_records_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_usage_records_task_id_tasks"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_usage_records_agent_run_id_agent_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_records")),
        sa.UniqueConstraint("call_id", "attempt", name=op.f("uq_usage_records_call_id_attempt")),
    )
    op.create_index("ix_usage_records_project_id_created_at", "usage_records", ["project_id", "created_at"])
    op.create_index("ix_usage_records_task_id_created_at", "usage_records", ["task_id", "created_at"])
    op.create_index("ix_usage_records_agent_run_id", "usage_records", ["agent_run_id"])
    op.alter_column(
        "agent_runs",
        "estimated_cost",
        type_=sa.Numeric(precision=20, scale=10),
        existing_type=sa.Numeric(precision=12, scale=6),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Back to the 0006 precision: costs below 0.000001 round to six places again.
    op.alter_column(
        "agent_runs",
        "estimated_cost",
        type_=sa.Numeric(precision=12, scale=6),
        existing_type=sa.Numeric(precision=20, scale=10),
        existing_nullable=True,
    )
    op.drop_table("usage_records")
