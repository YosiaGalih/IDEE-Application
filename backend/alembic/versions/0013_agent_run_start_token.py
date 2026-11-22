"""agent_runs.start_token: the token of the request that started a run (WP-3.2c2a; spec WP-3.2 §16.3 Keputusan
Lead #66, §16.3a #1-#4). A retried start is the row's only while task, agent and token all match, and the
minimal end (``fail``) ends only the row of its token. Rows written before this revision keep a NULL token:
no retried start or minimal end ever matches them.

Revision ID: 0013_agent_run_start_token
Revises: 0012_messages_task_context_refs
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_agent_run_start_token"
down_revision: str | Sequence[str] | None = "0012_messages_task_context_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("start_token", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "start_token")
