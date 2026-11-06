"""Agents: registry sync (FR-041) and the catalog read side (FR-044 to FR-046; spec WP-3.1b goals 6, 7)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, get_args

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.readiness import AgentStatus, StatusReason, assess
from app.agents.registry import AgentRegistry, AgentSpec
from app.core.audit import AuditWriter
from app.core.logging import get_logger
from app.models.agents import AgentProfile, AgentRun
from app.models.enums import AGENT_GROUPS, AGENT_MODES
from app.models.projects import Project
from app.models.tasks import Task
from app.services.projects import Actor, visible_projects_filter

logger = get_logger(__name__)

# One sync at a time across every process (API replicas starting together, seed-agents): held to commit.
_SYNC_LOCK = text("SELECT pg_advisory_xact_lock(hashtext('kk.agent_registry'))")


@dataclass(frozen=True, slots=True)
class SyncResult:
    inserted: int
    updated: int
    retired: int
    unchanged: int

    @property
    def changed(self) -> bool:
        return bool(self.inserted or self.updated or self.retired)


def _values(spec: AgentSpec) -> dict[str, Any]:
    data = spec.model_dump(mode="json")
    return {
        "agent_id": spec.agent_id,
        "slug": spec.slug,
        "name": spec.name,
        "group": spec.group,
        "version": spec.version,
        "enabled": spec.enabled,
        "mvp_mode": spec.mvp_mode,
        "priority": spec.priority,
        "description": spec.description,
        "purpose": spec.purpose,
        "triggers": data["triggers"],
        "capabilities": data["capabilities"],
        "tools": data["tools"],
        "permission_profile": data["permissions"],
        "model_tier": spec.model_tier,
        "limits": data["limits"],
        "runtime_agent": spec.runtime_agent,
        "input_contract": spec.input_contract,
        "output_contract": spec.output_contract,
        "inputs_tools_note": spec.inputs_tools_note,
        "output_note": spec.output_note,
        "access_note": spec.access_note,
        "target_mode": spec.target_mode,
        "phase": spec.phase,
        "contract_status": spec.contract_status,
        "notes": data["notes"],
        "registry_hash": spec.canonical_hash(),
        "in_registry": True,
    }


def _differs(row: AgentProfile, values: Mapping[str, Any]) -> bool:
    # Every column, not only the stored hash: a row edited in place is put back too.
    return any(getattr(row, field) != value for field, value in values.items())


async def sync_registry(session: AsyncSession, registry: AgentRegistry, audit: AuditWriter) -> SyncResult:
    """Make ``agent_profiles`` match the registry; the caller commits. Rows are never deleted: an id that
    left the registry is kept with ``in_registry`` and ``enabled`` false (ids are never reused; runs point at
    them). The audit event names every agent that changed, with its versions and hashes."""
    await session.execute(_SYNC_LOCK)
    rows = {row.agent_id: row for row in (await session.scalars(select(AgentProfile))).all()}
    wanted = {spec.agent_id: (spec, _values(spec)) for spec in registry.agents}
    retired = sorted(
        aid for aid, row in rows.items() if aid not in wanted and (row.in_registry or row.enabled)
    )
    stale = sorted(aid for aid in wanted if aid in rows and _differs(rows[aid], wanted[aid][1]))
    inserted = sorted(aid for aid in wanted if aid not in rows)
    for aid in retired:
        rows[aid].in_registry, rows[aid].enabled = False, False
    # A row taking another slug leaves the live set first, so a slug freed in this same sync (by a retired
    # agent or by a swap) can be taken: slugs are unique among live rows only.
    for aid in stale:
        if rows[aid].slug != wanted[aid][1]["slug"]:
            rows[aid].in_registry = False
    await session.flush()
    updated: list[dict[str, Any]] = []
    not_bumped: list[str] = []
    for aid in stale:
        row, (spec, values) = rows[aid], wanted[aid]
        updated.append(
            {
                "agent_id": aid,
                "version": [row.version, spec.version],
                "hash": [row.registry_hash, values["registry_hash"]],
            }
        )
        if row.registry_hash != values["registry_hash"] and spec.version <= row.version:
            not_bumped.append(aid)
        for field, value in values.items():
            setattr(row, field, value)
    for aid in inserted:
        session.add(AgentProfile(**wanted[aid][1]))
    await session.flush()
    result = SyncResult(
        inserted=len(inserted),
        updated=len(stale),
        retired=len(retired),
        unchanged=len(wanted) - len(stale) - len(inserted),
    )
    if not_bumped:
        # agent_runs.agent_version no longer names one behaviour for these ids.
        logger.warning("agent_registry_version_not_bumped", agent_ids=not_bumped)
    if result.changed:
        await audit.write(
            session,
            actor_type="system",
            actor_id="agent-registry",
            action="agent.registry_synced",
            result="success",
            resource_type="agent_registry",
            metadata={
                "inserted": inserted,
                "updated": updated,
                "retired": retired,
                "unchanged": result.unchanged,
                "version_not_bumped": not_bumped,
            },
        )
    return result


# --- catalog (FR-044, FR-045) ------------------------------------------------------------------------------

STATUSES: tuple[AgentStatus, ...] = get_args(AgentStatus)
RECENT_RUNS = 10
# Runs with a result. ``running`` and ``approval_required`` (paused, ADR-025) have none yet.
FINISHED_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled", "budget_exceeded"})


@dataclass(frozen=True, slots=True)
class AgentView:
    profile: AgentProfile
    status: AgentStatus
    status_reasons: list[StatusReason]
    unavailable_tools: list[str]


@dataclass(frozen=True, slots=True)
class Counts:
    mode: dict[str, int]
    status: dict[str, int]


@dataclass(frozen=True, slots=True)
class RunSummary:
    id: uuid.UUID
    task_id: uuid.UUID
    project_id: uuid.UUID
    agent_id: str
    agent_name: str
    status: str
    started_at: datetime
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class RunStats:
    runs: int
    success_rate: float | None  # succeeded / finished runs; None while no run has finished
    avg_duration_ms: int | None


@dataclass(frozen=True, slots=True)
class AgentDetail:
    agent: AgentView
    recent_runs: list[RunSummary]
    stats: RunStats


@dataclass(frozen=True, slots=True)
class _Facts:
    mvp_mode: str
    group: str
    tools: tuple[str, ...]


def _view(profile: AgentProfile, provider_ready: bool | None) -> AgentView:
    facts = _Facts(profile.mvp_mode, profile.group, tuple(profile.tools))
    status, reasons, unavailable = assess(facts, enabled=profile.enabled, provider_ready=provider_ready)
    return AgentView(profile, status, reasons, unavailable)


def _matches(view: AgentView, q: str) -> bool:
    p = view.profile
    needle = q.casefold()
    haystack = (p.agent_id, p.slug, p.name, *p.tools)
    return any(needle in value.casefold() for value in haystack)


async def list_agents(
    session: AsyncSession,
    *,
    group: str | None = None,
    mode: str | None = None,
    status: AgentStatus | None = None,
    q: str | None = None,
    provider_ready: bool | None = None,
) -> tuple[list[AgentView], Counts]:
    """The agents of the current registry, in catalog order (group, then id). ``Counts`` cover the whole
    registry, so the filter chips keep their numbers while a filter is applied."""
    rows = (await session.scalars(select(AgentProfile).where(AgentProfile.in_registry))).all()
    views = sorted(
        (_view(row, provider_ready) for row in rows),
        key=lambda v: (AGENT_GROUPS.index(v.profile.group), v.profile.agent_id),
    )
    counts = Counts(
        mode={m: sum(1 for v in views if v.profile.mvp_mode == m) for m in AGENT_MODES},
        status={st: sum(1 for v in views if v.status == st) for st in STATUSES},
    )
    selected = [
        v
        for v in views
        if (group is None or v.profile.group == group)
        and (mode is None or v.profile.mvp_mode == mode)
        and (status is None or v.status == status)
        and (not q or _matches(v, q))
    ]
    return selected, counts


async def get_agent(
    session: AsyncSession, actor: Actor, agent_id: str, *, provider_ready: bool | None = None
) -> AgentDetail | None:
    """One agent with its recent runs and run statistics, counting only runs of tasks in projects the actor
    can see (AC-01): the catalog is shared, the runs are not."""
    profile = await session.get(AgentProfile, agent_id)
    if profile is None or not profile.in_registry:  # the catalog is the registry as it is now (list agrees)
        return None
    visible = (
        select(AgentRun.id)
        .join(Task, Task.id == AgentRun.task_id)
        .join(Project, Project.id == Task.project_id)
        .where(AgentRun.agent_id == agent_id, visible_projects_filter(actor))
    )
    recent = (
        await session.execute(
            select(AgentRun, Task.project_id)
            .join(Task, Task.id == AgentRun.task_id)
            .where(AgentRun.id.in_(visible))
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .limit(RECENT_RUNS)
        )
    ).all()
    totals = (
        await session.execute(
            select(
                AgentRun.status,
                func.count(),
                func.sum(AgentRun.duration_ms),
                func.count(AgentRun.duration_ms),
            )
            .where(AgentRun.id.in_(visible))
            .group_by(AgentRun.status)
        )
    ).all()
    return AgentDetail(
        agent=_view(profile, provider_ready),
        recent_runs=[
            RunSummary(
                id=run.id,
                task_id=run.task_id,
                project_id=project_id,
                agent_id=run.agent_id,
                agent_name=profile.name,
                status=run.status,
                started_at=run.started_at,
                duration_ms=run.duration_ms,
            )
            for run, project_id in recent
        ],
        stats=_stats(totals),
    )


def _stats(totals: Sequence[tuple[str, int, int | None, int]]) -> RunStats:
    """From one grouped query: (status, runs, sum of durations, runs with a duration) per status."""
    runs = sum(count for _, count, _, _ in totals)
    finished = sum(count for status, count, _, _ in totals if status in FINISHED_RUN_STATUSES)
    succeeded = sum(count for status, count, _, _ in totals if status == "succeeded")
    timed = sum(with_duration for _, _, _, with_duration in totals)
    total_ms = sum(duration or 0 for _, _, duration, _ in totals)
    return RunStats(
        runs=runs,
        success_rate=succeeded / finished if finished else None,
        avg_duration_ms=round(total_ms / timed) if timed else None,
    )
