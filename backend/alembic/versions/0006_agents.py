"""agent_profiles (the agent registry, synced) and agent_runs (DATA_MODEL M07, WP-3.1b).

agent_runs.status has approval_required from the start (ADR-025); agent_runs.cost_status follows API §11.2.

Revision ID: 0006_agents
Revises: 0005_repo_index_entries
Create Date: 2026-11-05 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_agents"
down_revision: str | Sequence[str] | None = "0005_repo_index_entries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GROUPS = (
    "planning_intent",
    "software_development",
    "testing_debugging",
    "quality_security",
    "git_collaboration",
    "devops_operations",
    "documentation_knowledge",
    "specialized_engineering",
    "orchestration",
    "ai_assistant",
    "integrations",
    "portfolio",
)
RUN_STATUSES = ("running", "succeeded", "failed", "cancelled", "budget_exceeded", "approval_required")
COST_STATUSES = ("estimated", "partial", "actual", "unavailable")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "agent_profiles",
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("group", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("mvp_mode", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=4), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("triggers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tools", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("permission_profile", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_tier", sa.String(length=16), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("runtime_agent", sa.String(length=40), nullable=True),
        sa.Column("input_contract", sa.String(length=60), nullable=True),
        sa.Column("output_contract", sa.String(length=60), nullable=True),
        sa.Column("inputs_tools_note", sa.Text(), nullable=False),
        sa.Column("output_note", sa.Text(), nullable=False),
        sa.Column("access_note", sa.Text(), nullable=False),
        sa.Column("target_mode", sa.String(length=16), nullable=True),
        sa.Column("phase", sa.String(length=8), nullable=True),
        sa.Column("contract_status", sa.String(length=16), nullable=True),
        sa.Column("notes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("registry_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("in_registry", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in('"group"', GROUPS), name=op.f("ck_agent_profiles_group")),
        sa.CheckConstraint(
            _in("mvp_mode", ("llm", "profile", "tool", "planned")), name=op.f("ck_agent_profiles_mvp_mode")
        ),
        sa.CheckConstraint(_in("priority", ("P0", "P1", "P2")), name=op.f("ck_agent_profiles_priority")),
        sa.CheckConstraint(
            _in("model_tier", ("fast", "default", "complex")), name=op.f("ck_agent_profiles_model_tier")
        ),
        sa.CheckConstraint(
            "target_mode IS NULL OR " + _in("target_mode", ("llm", "profile", "tool")),
            name=op.f("ck_agent_profiles_target_mode"),
        ),
        sa.PrimaryKeyConstraint("agent_id", name=op.f("pk_agent_profiles")),
        sa.UniqueConstraint("slug", name=op.f("uq_agent_profiles_slug")),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.String(length=40), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(length=40), nullable=False),
        sa.Column("model_provider", sa.String(length=40), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.CHAR(length=12), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="running", nullable=False),
        sa.Column(
            "input",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("iterations", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("cost_status", sa.String(length=16), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("status", RUN_STATUSES), name=op.f("ck_agent_runs_status")),
        sa.CheckConstraint(
            "cost_status IS NULL OR " + _in("cost_status", COST_STATUSES),
            name=op.f("ck_agent_runs_cost_status"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_profiles.agent_id"],
            name=op.f("fk_agent_runs_agent_id_agent_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_agent_runs_task_id_tasks"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"])
    op.create_index("ix_agent_runs_agent_id_started_at", "agent_runs", ["agent_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_agent_id_started_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_task_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("agent_profiles")
