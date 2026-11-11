"""Ledger rows for tests, written through the real writer (``app.services.usage.UsageLedger``)."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import make_sessionmaker
from app.llm.base import ErrorKind, Usage
from app.llm.pricing import load_pricing
from app.llm.retry import AttemptRecord
from app.services.usage import UsageCallContext, UsageLedger

HAIKU = "claude-haiku-5-5"
PRICING = load_pricing(Path(__file__).resolve().parents[1] / "config" / "pricing.yaml")


async def record(
    engine: AsyncEngine,
    project_id: uuid.UUID,
    *,
    task_id: uuid.UUID | None = None,
    agent_id: str | None = None,
    model: str = HAIKU,
    status: str = "ok",
    error_kind: ErrorKind | None = None,
    usage: Usage | None = Usage(input_tokens=100, output_tokens=20),  # noqa: B008 - frozen
    at: datetime | None = None,
) -> uuid.UUID:
    """One attempt; ``at`` moves its created_at afterwards (the writer stamps now())."""
    ledger = UsageLedger(make_sessionmaker(engine), PRICING)
    context = UsageCallContext(
        project_id=project_id,
        call_id=uuid.uuid4(),
        input_token_bound=1_000,
        output_token_cap=8_192,
        task_id=task_id,
        agent_id=agent_id,
    )
    attempt = AttemptRecord("scripted", model, 1, "req_usage", usage, 7, status, error_kind)  # type: ignore[arg-type]
    row_id = await ledger.record(context, attempt)
    if at is not None:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE usage_records SET created_at = :at WHERE id = :id"), {"at": at, "id": row_id}
            )
    return row_id
