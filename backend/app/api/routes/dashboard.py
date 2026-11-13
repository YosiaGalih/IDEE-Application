"""Dashboard route (API_CONTRACTS §3 ``GET /dashboard``; WP-2.1e, conflict #70). Thin.

Any signed-in user; the service limits every count and list to the projects the user can see (AC-01). Usage
comes from the ledger (WP-3.1f1); money is a plain decimal string or ``null`` (``Money``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import CurrentActor
from app.api.routes.projects import EnvDep, ProjectOut, _respond
from app.api.routes.usage_out import CostStatus
from app.core.money import Money
from app.services import dashboard
from app.services.dashboard import Dashboard, TaskSummaryView
from app.services.usage import EMPTY_TOTALS, UsageTotals

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class UsageOut(BaseModel):
    input_tokens: int | None  # null: rows exist but none reported tokens (unknown, never 0)
    output_tokens: int | None
    estimated_cost: Money | None  # null only when no row could be priced (AC-16)
    cost_status: CostStatus

    @classmethod
    def of(cls, totals: UsageTotals) -> UsageOut:
        return cls(
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            estimated_cost=totals.estimated_cost,
            cost_status=totals.cost_status,
        )


class PeriodOut(BaseModel):
    start: datetime
    end: datetime


class DashboardUsageOut(UsageOut):
    period: PeriodOut


class TaskSummaryOut(BaseModel):
    """``TaskSummary`` (API_CONTRACTS §11 #2) + ``project_name`` (conflict #70)."""

    id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    title: str
    status: str
    status_reason: str | None
    current_step: str | None
    workflow_type: str
    priority: str
    iteration: int
    usage: UsageOut
    has_pending_approval: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, view: TaskSummaryView, usage: UsageTotals) -> TaskSummaryOut:
        return cls(
            id=view.id,
            project_id=view.project_id,
            project_name=view.project_name,
            title=view.title,
            status=view.status,
            status_reason=view.status_reason,
            current_step=view.current_step,
            workflow_type=view.workflow_type,
            priority=view.priority,
            iteration=view.iteration,
            usage=UsageOut.of(usage),
            has_pending_approval=view.has_pending_approval,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


class MetricsOut(BaseModel):
    active_projects: int
    running_tasks: int
    completed_tasks: int
    failed_tasks: int
    waiting_approval: int


class DashboardOut(BaseModel):
    metrics: MetricsOut
    recent_projects: list[ProjectOut]
    recent_tasks: list[TaskSummaryOut]
    failed_queue: list[TaskSummaryOut]
    agent_activity: list[dict[str, object]]  # AgentRunSummary; empty until agent runs are recorded
    usage: DashboardUsageOut

    @classmethod
    def of(cls, board: Dashboard) -> DashboardOut:
        start, end = board.period

        def summary(view: TaskSummaryView) -> TaskSummaryOut:
            return TaskSummaryOut.of(view, board.task_usage.get(view.id, EMPTY_TOTALS))

        return cls(
            metrics=MetricsOut(**board.metrics),
            recent_projects=[ProjectOut.of(view) for view in board.recent_projects],
            recent_tasks=[summary(view) for view in board.recent_tasks],
            failed_queue=[summary(view) for view in board.failed_queue],
            agent_activity=[],
            usage=DashboardUsageOut(
                input_tokens=board.usage.input_tokens,
                output_tokens=board.usage.output_tokens,
                estimated_cost=board.usage.estimated_cost,
                cost_status=board.usage.cost_status,
                period=PeriodOut(start=start, end=end),
            ),
        )


@router.get("")
async def get_dashboard(request: Request, actor: CurrentActor, env: EnvDep) -> JSONResponse:
    board = await dashboard.load_dashboard(env, actor)
    return _respond(
        request,
        DashboardOut.of(board).model_dump(mode="json"),
        meta={"unavailable": list(dashboard.UNAVAILABLE), "failed_queue_total": board.failed_queue_total},
    )
