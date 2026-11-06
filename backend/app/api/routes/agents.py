"""Agent catalog routes (API_CONTRACTS §6; FR-044 to FR-046 read side; spec WP-3.1b goal 7). Thin.

Any signed-in user reads the catalog. Runs inside an agent's detail are limited to the caller's projects by
the service (AC-01).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents.readiness import AgentStatus, StatusReason
from app.agents.registry import AGENT_ID_PATTERN, Group, Mode
from app.api.deps import CurrentActor
from app.api.routes.projects import EnvDep, _respond
from app.core.errors import AppError
from app.services import agents
from app.services.agents import AgentDetail, AgentView, RunStats, RunSummary

router = APIRouter(prefix="/agents", tags=["agents"])
_SEARCH = r"^[^\x00-\x1f\x7f-\x9f]*$"  # no C0/C1 control characters (a NUL would reach PostgreSQL)


class AgentOut(BaseModel):
    """``AgentProfile`` (API §6) + the FR-042 fields, the v2 fields, and the reasons behind its status."""

    agent_id: str
    slug: str
    name: str
    group: str
    version: int
    enabled: bool
    mvp_mode: str
    priority: str
    description: str
    purpose: str
    triggers: list[str]
    capabilities: list[str]
    tools: list[str]
    unavailable_tools: list[str]
    permission_profile: dict[str, str]
    model_tier: str
    limits: dict[str, Any]
    runtime_agent: str | None
    input_contract: str | None
    output_contract: str | None
    access_note: str
    target_mode: str | None
    phase: str | None
    contract_status: str | None  # "planned": the contract names belong to a planned agent
    status: AgentStatus
    status_reasons: list[StatusReason]

    @classmethod
    def of(cls, view: AgentView) -> AgentOut:
        p = view.profile
        return cls(
            agent_id=p.agent_id,
            slug=p.slug,
            name=p.name,
            group=p.group,
            version=p.version,
            enabled=p.enabled,
            mvp_mode=p.mvp_mode,
            priority=p.priority,
            description=p.description,
            purpose=p.purpose,
            triggers=list(p.triggers),
            capabilities=list(p.capabilities),
            tools=list(p.tools),
            unavailable_tools=view.unavailable_tools,
            permission_profile=dict(p.permission_profile),
            model_tier=p.model_tier,
            limits=dict(p.limits),
            runtime_agent=p.runtime_agent,
            input_contract=p.input_contract,
            output_contract=p.output_contract,
            access_note=p.access_note,
            target_mode=p.target_mode,
            phase=p.phase,
            contract_status=p.contract_status,
            status=view.status,
            status_reasons=view.status_reasons,
        )


class RunSummaryOut(BaseModel):
    """``AgentRunSummary`` (API §11.2)."""

    id: uuid.UUID
    task_id: uuid.UUID
    project_id: uuid.UUID
    agent_id: str
    agent_name: str
    status: str
    started_at: datetime
    duration_ms: int | None

    @classmethod
    def of(cls, run: RunSummary) -> RunSummaryOut:
        return cls(**{field: getattr(run, field) for field in cls.model_fields})


class RunStatsOut(BaseModel):
    runs: int
    success_rate: float | None
    avg_duration_ms: int | None

    @classmethod
    def of(cls, stats: RunStats) -> RunStatsOut:
        return cls(runs=stats.runs, success_rate=stats.success_rate, avg_duration_ms=stats.avg_duration_ms)


class AgentDetailOut(AgentOut):
    """API §6: ``AgentProfile + {recent_runs, stats}``, one flat object."""

    recent_runs: list[RunSummaryOut]
    stats: RunStatsOut

    @classmethod
    def of_detail(cls, detail: AgentDetail) -> AgentDetailOut:
        return cls(
            **AgentOut.of(detail.agent).model_dump(),
            recent_runs=[RunSummaryOut.of(run) for run in detail.recent_runs],
            stats=RunStatsOut.of(detail.stats),
        )


@router.get("")
async def list_agents(
    request: Request,
    actor: CurrentActor,
    env: EnvDep,
    group: Group | None = None,
    mode: Mode | None = None,
    status: AgentStatus | None = None,
    q: Annotated[str | None, Query(max_length=100, pattern=_SEARCH)] = None,
) -> JSONResponse:
    del actor  # any signed-in user; the catalog holds no project data
    async with env.sessions() as session:
        views, counts = await agents.list_agents(session, group=group, mode=mode, status=status, q=q)
    return _respond(
        request,
        [AgentOut.of(view).model_dump(mode="json") for view in views],
        meta={"total": len(views), "counts": {"mode": counts.mode, "status": counts.status}},
    )


@router.get("/{agent_id}")
async def get_agent(
    request: Request,
    actor: CurrentActor,
    env: EnvDep,
    agent_id: str,
) -> JSONResponse:
    if not re.fullmatch(
        AGENT_ID_PATTERN, agent_id
    ):  # malformed ids are just unknown ones: 404, never a 422 echo
        raise AppError("NOT_FOUND", "Not found", http_status=404)
    async with env.sessions() as session:
        detail = await agents.get_agent(session, actor, agent_id)
    if detail is None:
        raise AppError("NOT_FOUND", "Not found", http_status=404)
    return _respond(request, AgentDetailOut.of_detail(detail).model_dump(mode="json"))
