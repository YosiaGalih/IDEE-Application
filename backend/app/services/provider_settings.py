"""Provider settings for owners and admins (API §9, §11 #8, §11.3; spec 3.1e Goal 5). Runs in the ``api``
process, which never holds the key: what it shows about the key is what the worker last reported, and a smoke
test is a worker job (Lead decisions 3.1e #2 to #4).

- ``model_policy`` and ``enabled`` are the only fields a request can change; ``base_url`` and the key are not
  (Lead decision 3.1e #6: pointing the key at another host is exfiltration).
- A smoke request takes the provider's smoke lock, queues the job, then waits at most ``SMOKE_WAIT_S`` for the
  report carrying its job id: the report (§11 #8) when it lands in time, the job id (202) otherwise, and a
  conflict (409) while another smoke runs.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.ratelimit import FixedWindowLimiter
from app.core.rbac import has_permission
from app.models.providers import ProviderConfig
from app.services import provider_readiness, smoke_lock
from app.services.job_queue import SmokeQueue
from app.services.projects import Actor, enforce_rate

logger = get_logger(__name__)

PERMISSION = "settings:providers"
SMOKE_POLL_S = 0.5
SMOKE_RATE_LIMIT = 20  # per user and hour: each run makes a few small paid calls
SMOKE_RATE_WINDOW_S = 3600
REPORT_KEYS = ("provider", "mode", "ready", "checks", "ran_at")  # the API §11 #8 shape


@dataclass(frozen=True, slots=True)
class ProviderSettingsEnv:
    sessions: async_sessionmaker[AsyncSession]
    audit: AuditWriter
    settings: Settings
    redis: Redis | None = None
    smoke_jobs: SmokeQueue | None = None
    limiter: FixedWindowLimiter | None = None  # None only where no HTTP caller exists (tests)
    poll_s: float = SMOKE_POLL_S


@dataclass(frozen=True, slots=True)
class ProviderView:
    row: ProviderConfig
    effective_models: dict[str, str]
    verdict: provider_readiness.ProviderVerdict


@dataclass(frozen=True, slots=True)
class SmokeOutcome:
    job_id: uuid.UUID
    report: dict[str, Any] | None  # None: still running when the wait ended


def _authorize(actor: Actor) -> None:
    if not has_permission(actor.grants, PERMISSION, user_id=actor.user_id):
        raise AppError("FORBIDDEN", "Not allowed", http_status=403)


def _view(row: ProviderConfig, settings: Settings) -> ProviderView:
    policy = dict(row.model_policy or {})
    return ProviderView(
        row=row,
        effective_models=provider_readiness.effective_models(settings, policy),
        verdict=provider_readiness.provider_ready(row, settings),  # the api holds no key: no rotation check
    )


async def _row(session: AsyncSession, provider_id: uuid.UUID, *, lock: bool = False) -> ProviderConfig:
    query = select(ProviderConfig).where(ProviderConfig.id == provider_id)
    row = (await session.scalars(query.with_for_update() if lock else query)).one_or_none()
    if row is None:
        raise AppError("NOT_FOUND", "Provider not found", http_status=404)
    return row


async def list_providers(env: ProviderSettingsEnv, actor: Actor) -> list[ProviderView]:
    _authorize(actor)
    query = select(ProviderConfig).order_by(ProviderConfig.provider)
    async with env.sessions() as session:
        rows = (await session.scalars(query)).all()
        if not any(row.provider == provider_readiness.PROVIDER for row in rows):
            # The configured vendor always has a row to show: made on the first read only.
            await provider_readiness.ensure_config(session)
            await session.commit()
            rows = (await session.scalars(query)).all()
        return [_view(row, env.settings) for row in rows]


async def get_provider(env: ProviderSettingsEnv, actor: Actor, provider_id: uuid.UUID) -> ProviderView:
    _authorize(actor)
    async with env.sessions() as session:
        return _view(await _row(session, provider_id), env.settings)


_UNSET: Any = object()


async def update_provider(
    env: ProviderSettingsEnv,
    actor: Actor,
    provider_id: uuid.UUID,
    *,
    model_policy: Mapping[str, Any] | None = _UNSET,
    enabled: bool = _UNSET,
) -> ProviderView:
    """``model_policy`` merges per alias (an empty or null value drops one); a change of the effective policy
    puts readiness back to ``unknown`` (ADR-032). Audited as ``provider.updated`` when something changed."""
    _authorize(actor)
    async with env.sessions() as session:
        row = await _row(session, provider_id, lock=True)
        changes: dict[str, list[Any]] = {}
        reset = False
        if model_policy is not _UNSET:
            before = dict(row.model_policy or {})
            try:
                after = provider_readiness.validate_model_policy({**before, **(model_policy or {})})
            except ValueError as exc:
                raise AppError(
                    "VALIDATION_ERROR",
                    "Input did not pass validation",
                    http_status=422,
                    details=[{"field": "model_policy", "issue": str(exc)}],
                ) from None
            if provider_readiness.set_model_policy(row, after):
                changes["model_policy"] = [before, after]
                reset = True
        if enabled is not _UNSET and enabled != row.enabled:
            changes["enabled"] = [row.enabled, enabled]
            row.enabled = enabled
        if changes:
            await env.audit.write(
                session,
                actor_type="user",
                actor_id=str(actor.user_id),
                action="provider.updated",
                result="success",
                resource_type="provider_config",
                resource_id=str(row.id),
                ip=actor.ip,
                metadata={"provider": row.provider, "changes": changes, "readiness_reset": reset},
            )
            await session.commit()
            await session.refresh(row)
        return _view(row, env.settings)


def readiness_of(row: ProviderConfig, settings: Settings, running_job_id: str | None) -> dict[str, Any]:
    """§11.3: the last report as recorded, the models it tested, and whether it counts now. ``ready`` is
    whether it counts now (never green for a report the models or mode moved away from; silent-failure
    3.1e1 M6); ``report_ready`` is the report's own result. ``job_id`` names the run that wrote it and
    ``running_job_id`` the run holding the lock: a 202 job id in neither has ended without a report."""
    report = dict(row.readiness_report or {})
    verdict = provider_readiness.provider_ready(row, settings)
    return {
        "provider": row.provider,
        "readiness_status": row.readiness_status,
        "checked_at": row.readiness_checked_at.isoformat() if row.readiness_checked_at else None,
        "mode": report.get("mode"),
        "ready": verdict.ready,
        "report_ready": bool(report.get("ready", False)),
        "checks": list(report.get("checks", [])),
        "models": report.get("models"),
        "job_id": report.get("job_id"),
        "running_job_id": running_job_id,
        "effective": {"state": verdict.state, "reason": verdict.reason},
    }


async def get_readiness(env: ProviderSettingsEnv, actor: Actor, provider_id: uuid.UUID) -> dict[str, Any]:
    _authorize(actor)
    async with env.sessions() as session:
        row = await _row(session, provider_id)
    running = await smoke_lock.holder(env.redis, row.provider) if env.redis is not None else None
    return readiness_of(row, env.settings, running)


async def request_smoke(
    env: ProviderSettingsEnv, actor: Actor, provider_id: uuid.UUID, *, wait_s: float | None = None
) -> SmokeOutcome:
    _authorize(actor)
    if env.redis is None or env.smoke_jobs is None:
        raise AppError("DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable", http_status=503)
    async with env.sessions() as session:
        row = await _row(session, provider_id)
        provider, row_id = row.provider, row.id
    job_id = uuid.uuid4()
    if not await smoke_lock.acquire(env.redis, provider, job_id):
        raise AppError("CONFLICT", "A smoke test is already running for this provider", http_status=409)
    try:
        # Counted only for a smoke that can start: a 409 uses up nothing (backend review 3.1e1 L5).
        await enforce_rate(
            env,
            actor,
            scope="provider-smoke",
            limit=SMOKE_RATE_LIMIT,
            window_s=SMOKE_RATE_WINDOW_S,
            message="Too many smoke tests; try again later",
        )
    except AppError:
        await smoke_lock.release(env.redis, provider, job_id)
        raise
    audit = {
        "actor_type": "user",
        "actor_id": str(actor.user_id),
        "action": "provider.smoke_requested",
        "resource_type": "provider_config",
        "resource_id": str(row_id),
        "ip": actor.ip,
    }
    try:
        await env.smoke_jobs.enqueue_provider_smoke(job_id=job_id, provider=provider)
    except Exception as exc:
        logger.error("provider_smoke_enqueue_failed", job_id=str(job_id), error_type=type(exc).__name__)
        await smoke_lock.release(env.redis, provider, job_id)
        await env.audit.write_detached(
            env.sessions, result="error", metadata={"provider": provider, "job_id": str(job_id),
                                                    "error": "queue_unavailable"}, **audit,
        )  # fmt: skip
        raise AppError(
            "DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable", http_status=503
        ) from None
    async with env.sessions() as session:
        await env.audit.write(
            session, result="success", metadata={"provider": provider, "job_id": str(job_id)}, **audit
        )
        await session.commit()
    report = await _wait_for_report(
        env, row_id, job_id, env.settings.smoke_wait_s if wait_s is None else wait_s
    )
    return SmokeOutcome(job_id, report)


async def _wait_for_report(
    env: ProviderSettingsEnv, row_id: uuid.UUID, job_id: uuid.UUID, wait_s: float
) -> dict[str, Any] | None:
    """The report the worker recorded for ``job_id``, polled from the row (there is no result backend)."""
    deadline = asyncio.get_running_loop().time() + wait_s
    while True:
        async with env.sessions() as session:
            row = await session.get(ProviderConfig, row_id)
            report = dict(row.readiness_report or {}) if row is not None else {}
        if report.get("job_id") == str(job_id):
            return {key: report.get(key) for key in REPORT_KEYS}
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(env.poll_s, remaining))
