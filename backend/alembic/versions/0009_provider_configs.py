"""provider_configs (WP-3.1e1: DATA_MODEL provider_configs + ADR-032 readiness + the worker-reported key
status, spec WP-3.1e Goal 1 and Keputusan #4).

Revision ID: 0009_provider_configs
Revises: 0008_tool_calls
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_provider_configs"
down_revision: str | Sequence[str] | None = "0008_tool_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVIDERS = ("anthropic",)
READINESS_STATUSES = ("unknown", "passed", "failed")
SECRET_STATUSES = ("unknown", "configured", "missing")
LLM_PROVIDER_MODES = ("anthropic", "scripted")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "provider_configs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=True),
        sa.Column(
            "model_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "secret_ref", sa.String(length=100), server_default=sa.text("'ANTHROPIC_API_KEY'"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("pricing_version", sa.String(length=40), nullable=True),
        sa.Column("llm_provider_mode", sa.String(length=16), nullable=True),
        sa.Column(
            "readiness_status", sa.String(length=16), server_default=sa.text("'unknown'"), nullable=False
        ),
        sa.Column("readiness_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "readiness_report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("secret_status", sa.String(length=16), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("secret_hint", sa.String(length=40), nullable=True),
        sa.Column("secret_fingerprint", sa.CHAR(length=16), nullable=True),
        sa.Column("secret_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(_in("provider", PROVIDERS), name=op.f("ck_provider_configs_provider")),
        sa.CheckConstraint(
            _in("readiness_status", READINESS_STATUSES), name=op.f("ck_provider_configs_readiness_status")
        ),
        sa.CheckConstraint(
            _in("secret_status", SECRET_STATUSES), name=op.f("ck_provider_configs_secret_status")
        ),
        sa.CheckConstraint(
            f"llm_provider_mode IS NULL OR {_in('llm_provider_mode', LLM_PROVIDER_MODES)}",
            name=op.f("ck_provider_configs_llm_provider_mode"),
        ),
        sa.CheckConstraint(
            "secret_fingerprint IS NULL OR secret_fingerprint ~ '^[0-9a-f]{16}$'",
            name=op.f("ck_provider_configs_secret_fingerprint"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_provider_configs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_configs")),
        sa.UniqueConstraint("provider", name=op.f("uq_provider_configs_provider")),
    )


def downgrade() -> None:
    op.drop_table("provider_configs")
