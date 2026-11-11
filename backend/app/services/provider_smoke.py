"""The Claude smoke test (spec 3.1e Goals 3, 4; RUNBOOK §4): run the readiness gate and record its report.

Worker and CLI only. This module builds the provider and so reads ``ANTHROPIC_API_KEY``; the ``api`` process
never imports it (lint-imports contract "the api process never holds the key", Lead decision 3.1e #2) and
asks for a smoke through the job queue instead.

One smoke per provider at a time across every process (``smoke_lock``); the caller holds the lock.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.logging import get_logger
from app.llm.factory import ProviderNotConfigured, build_provider
from app.llm.pricing import PricingError, PricingTable, load_pricing
from app.llm.readiness import ReadinessReport, aborted_report, run_readiness
from app.services import provider_readiness, smoke_lock

logger = get_logger(__name__)

# A run makes at most seven calls of CALL_TIMEOUT_S + CALL_GRACE_S each (140 s). Its own limit ends it with a
# failed report before the job's hard limit kills it, and the job's limit ends before the lock it extended
# at start (smoke_lock.LOCK_TTL_S) expires (silent-failure 3.1e1 M3, M5).
SMOKE_RUN_LIMIT_S = 150.0
SMOKE_TIME_LIMIT_S = 180


@dataclass(frozen=True, slots=True)
class SmokeEnv:
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]
    audit: AuditWriter
    transport: Any = None  # test seam: an httpx2 transport for the Anthropic adapter


def _pricing(settings: Settings) -> PricingTable | PricingError:
    try:
        return load_pricing(Path(settings.pricing_path))
    except PricingError as exc:
        return exc  # a check fails with it; the run goes on


async def run_smoke(
    env: SmokeEnv, *, actor_id: str, job_id: uuid.UUID, provider: str = provider_readiness.PROVIDER
) -> ReadinessReport:
    """Run the gate against the configured provider and record the report (``provider.readiness_recorded``).
    The caller holds the lock."""
    settings = env.settings
    async with env.sessions() as session:
        row = await provider_readiness.ensure_config(session, provider)
        policy = dict(row.model_policy or {})
        await session.commit()
    models = provider_readiness.effective_models(settings, policy)
    pricing = _pricing(settings)
    key = settings.anthropic_api_key

    def aborted(reason: str) -> ReadinessReport:
        return aborted_report(
            reason, models=models, production=settings.is_production, pricing=pricing, provider_name=provider,
            mode=settings.llm_provider, key=key,
        )  # fmt: skip

    try:
        async with asyncio.timeout(SMOKE_RUN_LIMIT_S):
            llm = build_provider(settings, transport=env.transport)
            report = await run_readiness(
                llm, models=models, key=key, production=settings.is_production, pricing=pricing,
                provider_name=provider,
            )  # fmt: skip
    except ProviderNotConfigured as exc:
        report = aborted(str(exc))
    except TimeoutError:
        report = aborted(f"the smoke did not finish within {SMOKE_RUN_LIMIT_S:g} s")
    except Exception as exc:
        # Recorded as a failed report, never as "still running"; the message may echo the key, so only the
        # type is kept.
        logger.error("provider_smoke_aborted", job_id=str(job_id), error_type=type(exc).__name__)
        report = aborted(f"the smoke could not run: {type(exc).__name__}")
    async with env.sessions() as session:
        await provider_readiness.record_report(
            session, report, key=key, base_url=settings.anthropic_base_url,
            pricing_version=pricing.version if isinstance(pricing, PricingTable) else None, models=models,
            settings=settings, audit=env.audit, actor_id=actor_id, job_id=job_id,
        )  # fmt: skip
        await session.commit()
    return report


async def release_quietly(redis: Redis, provider: str, job_id: uuid.UUID) -> None:
    """Free the lock after a run; a Redis error here must not hide the report (it expires on its own)."""
    try:
        await smoke_lock.release(redis, provider, job_id)
    except RedisError as exc:
        logger.warning("provider_smoke_lock_not_released", job_id=str(job_id), error_type=type(exc).__name__)


def render(report: ReadinessReport) -> str:
    """The report for a terminal: already redacted, like every detail."""
    lines = [
        f"provider {report.provider} · mode {report.mode} · ready: {'yes' if report.ready else 'no'} · "
        f"{report.ran_at.isoformat()}"
    ]
    width = max(len(check.key) for check in report.checks)
    lines += [f"  {check.status:<8} {check.key:<{width}}  {check.detail}" for check in report.checks]
    return "\n".join(lines)
