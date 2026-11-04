"""Dashboard route (API_CONTRACTS §3 ``GET /dashboard``; WP-2.1e, conflict #70). Thin.

Any signed-in user; the service limits every count and list to the projects the user can see (AC-01).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.deps import CurrentActor
from app.api.routes.projects import EnvDep, ProjectOut, _respond
from app.services import dashboard
from app.services.dashboard import Dashboard, TaskSummaryView

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

CostStatus = Literal["estimated", "partial", "actual", "unavailable"]


class UsageOut(BaseModel):
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    cost_status: CostStatus

    @classmethod
    def unavailable(cls) -> UsageOut:
        # No usage ledger yet (usage_records, WP-3.1): unknown, never 0 (AC-16).
        return cls(input_tokens=None, output_tokens=None, estimated_cost=None, cost_status="unavailable")


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
    def of(cls, view: TaskSummaryView) -> TaskSummaryOut:
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
            usage=UsageOut.unavailable(),
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
        return cls(
            metrics=MetricsOut(**board.metrics),
            recent_projects=[ProjectOut.of(view) for view in board.recent_projects],
            recent_tasks=[TaskSummaryOut.of(view) for view in board.recent_tasks],
            failed_queue=[TaskSummaryOut.of(view) for view in board.failed_queue],
            agent_activity=[],
            usage=DashboardUsageOut(
                **UsageOut.unavailable().model_dump(), period=PeriodOut(start=start, end=end)
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
