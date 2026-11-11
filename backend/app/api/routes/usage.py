"""``GET /usage`` (API_CONTRACTS §9, §11 #9; spec WP-3.1f Keputusan Lead #5, #7, #13). Thin.

Any signed-in user may call it; the service decides the scope: ``project_id`` needs ``project:read`` there,
``task_id`` needs ``task:read`` in the task's project, and without either the sum covers the projects the
user can see. Out of scope is 404, never 403. Money is a plain decimal string or ``null`` (``Money``).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import CurrentActor
from app.api.routes.projects import EnvDep, _respond
from app.core.money import Money
from app.models.usage import UsageRecord
from app.services import usage
from app.services.usage import PAGE_SIZE_DEFAULT, PAGE_SIZE_MAX, UsagePoint, UsageQuery, UsageTotals

router = APIRouter(prefix="/usage", tags=["usage"])

CostStatus = Literal["estimated", "partial", "actual", "unavailable"]


class UsageTotalsOut(BaseModel):
    input_tokens: int
    output_tokens: int
    estimated_cost: Money | None
    actual_cost: None = None  # reserved for provider-reported costs (ADR-025)
    cost_status: CostStatus

    @classmethod
    def of(cls, totals: UsageTotals) -> UsageTotalsOut:
        return cls(
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            estimated_cost=totals.estimated_cost,
            cost_status=totals.cost_status,
        )


class UsagePointOut(BaseModel):
    key: str | None  # YYYY-MM-DD (UTC), a model id, or an agent id (null: rows without an agent)
    input_tokens: int
    output_tokens: int
    estimated_cost: Money | None
    cost_status: CostStatus

    @classmethod
    def of(cls, point: UsagePoint) -> UsagePointOut:
        return cls(
            key=point.key,
            input_tokens=point.totals.input_tokens,
            output_tokens=point.totals.output_tokens,
            estimated_cost=point.totals.estimated_cost,
            cost_status=point.totals.cost_status,
        )


class UsageRecordOut(BaseModel):
    """``UsageRecord`` (API_CONTRACTS §11.2) + the attempt fields FR-133 asks to show (additive)."""

    id: uuid.UUID
    project_id: uuid.UUID
    task_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    agent_id: str | None
    call_id: uuid.UUID
    attempt: int
    retry_count: int
    status: str
    error_kind: str | None
    request_id: str | None
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_tokens: int | None
    cache_read_tokens: int | None
    estimated_cost: Money | None
    actual_cost: None = None
    cost_status: str  # ck_usage_records_cost_status
    pricing_version: str | None
    duration_ms: int
    created_at: datetime

    @classmethod
    def of(cls, row: UsageRecord) -> UsageRecordOut:
        return cls(
            id=row.id,
            project_id=row.project_id,
            task_id=row.task_id,
            agent_run_id=row.agent_run_id,
            agent_id=row.agent_id,
            call_id=row.call_id,
            attempt=row.attempt,
            retry_count=row.retry_count,
            status=row.status,
            error_kind=row.error_kind,
            request_id=row.request_id,
            provider=row.provider,
            model=row.model,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cache_creation_tokens=row.cache_creation_tokens,
            cache_read_tokens=row.cache_read_tokens,
            estimated_cost=row.estimated_cost,
            cost_status=row.cost_status,
            pricing_version=row.pricing_version,
            duration_ms=row.duration_ms,
            created_at=row.created_at,
        )


class UsageOut(BaseModel):
    totals: UsageTotalsOut
    series: list[UsagePointOut]
    records: list[UsageRecordOut]


@router.get("")
async def get_usage(
    request: Request,
    actor: CurrentActor,
    env: EnvDep,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    group_by: Literal["day", "model", "agent"] = "day",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=PAGE_SIZE_MAX)] = PAGE_SIZE_DEFAULT,
) -> JSONResponse:
    query = UsageQuery(
        project_id=project_id,
        task_id=task_id,
        start=from_,
        end=to,
        group_by=group_by,
        page=page,
        page_size=page_size,
    )
    found = await usage.usage_report(env, actor, query)
    body = UsageOut(
        totals=UsageTotalsOut.of(found.totals),
        series=[UsagePointOut.of(point) for point in found.series],
        records=[UsageRecordOut.of(row) for row in found.records],
    )
    meta = {
        "page": found.page,
        "page_size": found.page_size,
        "total": found.total,
        "from": found.start.isoformat(),
        "to": found.end.isoformat(),
        "group_by": found.group_by,
    }
    return _respond(request, body.model_dump(mode="json"), meta=meta)
