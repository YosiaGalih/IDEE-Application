"""The Claude smoke test (spec 3.1e Goals 3, 4; RUNBOOK §4): run the readiness gate and record its report.

Worker and CLI only. This module builds the provider and so reads ``ANTHROPIC_API_KEY``; the ``api`` process
never imports it (lint-imports contract "the api process never holds the key", Lead decision 3.1e #2) and
asks for a smoke through the job queue instead.

One smoke per provider at a time across every process (``smoke_lock``); the caller holds the lock.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditWriter
from app.core.config import Settings
from app.llm.factory import ProviderNotConfigured, build_provider
from app.llm.pricing import PricingError, PricingTable, load_pricing
from app.llm.readiness import ReadinessReport, run_readiness, unconfigured_report
from app.services import provider_readiness

# A run makes about seven small calls of at most CALL_TIMEOUT_S each; the job's time limit ends it before
# its lock expires (smoke_lock.LOCK_TTL_S), so a lock never outlives its run.
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
    try:
        llm = build_provider(settings, transport=env.transport)
    except ProviderNotConfigured as exc:
        report = unconfigured_report(
            str(exc), models=models, production=settings.is_production, pricing=pricing,
            provider_name=provider, mode=settings.llm_provider,
        )  # fmt: skip
    else:
        report = await run_readiness(
            llm, models=models, key=key, production=settings.is_production, pricing=pricing,
            provider_name=provider,
        )  # fmt: skip
    async with env.sessions() as session:
        await provider_readiness.record_report(
            session, report, key=key, base_url=settings.anthropic_base_url,
            pricing_version=pricing.version if isinstance(pricing, PricingTable) else None, models=models,
            audit=env.audit, actor_id=actor_id, job_id=job_id,
        )  # fmt: skip
        await session.commit()
    return report


def render(report: ReadinessReport) -> str:
    """The report for a terminal: already redacted, like every detail."""
    lines = [
        f"provider {report.provider} · mode {report.mode} · ready: {'yes' if report.ready else 'no'} · "
        f"{report.ran_at.isoformat()}"
    ]
    width = max(len(check.key) for check in report.checks)
    lines += [f"  {check.status:<8} {check.key:<{width}}  {check.detail}" for check in report.checks]
    return "\n".join(lines)
