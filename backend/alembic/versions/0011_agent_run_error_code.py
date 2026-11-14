"""agent_runs.error_code: why a run failed, as a closed set of codes (WP-3.1f2a; spec WP-3.1f Keputusan Lead
#15, #35). ``agent_runs.error`` stays the redacted text; the code is set exactly when the run failed.

Revision ID: 0011_agent_run_error_code
Revises: 0010_usage_records
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_agent_run_error_code"
down_revision: str | Sequence[str] | None = "0010_usage_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Copied, not imported: a migration keeps the values it was written with (app.models.enums may move on).
ERROR_CODES = (
    "PROVIDER_DISABLED",
    "provider_not_ready",
    "provider_auth",
    "usage_unrecorded",
    "invalid_output",
    "iteration_limit",
    "timeout",
    "lease_lost",
    "model_error",
    "internal_error",
    "worker_cancelled",
    "interrupted",
)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("error_code", sa.String(length=40), nullable=True))
    # No writer existed before this revision, so production has no failed row; any other database gets the
    # recovery code for one, which keeps the CHECKs below valid.
    op.execute("UPDATE agent_runs SET error_code = 'interrupted' WHERE status = 'failed'")
    op.create_check_constraint(
        op.f("ck_agent_runs_error_code"),
        "agent_runs",
        f"error_code IS NULL OR {_in('error_code', ERROR_CODES)}",
    )
    op.create_check_constraint(
        op.f("ck_agent_runs_failed_has_code"), "agent_runs", "status <> 'failed' OR error_code IS NOT NULL"
    )
    op.create_check_constraint(
        op.f("ck_agent_runs_code_only_when_failed"), "agent_runs", "error_code IS NULL OR status = 'failed'"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_agent_runs_code_only_when_failed"), "agent_runs", type_="check")
    op.drop_constraint(op.f("ck_agent_runs_failed_has_code"), "agent_runs", type_="check")
    op.drop_constraint(op.f("ck_agent_runs_error_code"), "agent_runs", type_="check")
    op.drop_column("agent_runs", "error_code")
