"""Provider readiness (ADR-032, FR-045, FR-128, FR-130, FR-158; spec 3.1e Goals 1, 6).

The ``provider_configs`` row holds the last readiness report and the key status *as the worker saw it*: the
``api`` process never holds the key (Lead decision 3.1e #2, #4), so ``secret_status``, ``secret_hint`` and
``secret_fingerprint`` come from the worker (CLI, smoke job, or the runtime in 3.1f). This module never
reads ``Settings.anthropic_api_key``; a caller that holds the key passes it in.

``provider_ready`` is the predicate 3.1f enforces before any model call; ``provider_calls_allowed`` is the
per-project kill switch; ``resolve_model`` maps an alias to the model id (``model_policy`` over
``CLAUDE_MODEL_*``, Lead decision 3.1e #5).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditWriter
from app.core.config import Settings
from app.llm.readiness import ProviderState, ReadinessReport
from app.models.projects import Project
from app.models.providers import ProviderConfig
from app.services.projects import KILL_SWITCH_KEY

PROVIDER = "anthropic"
MODEL_ALIASES: tuple[str, ...] = ("default", "fast", "complex")
# A Claude model id as the API names them (claude-sonnet-5-5, claude-haiku-5-5-20261001); no id is hard-coded.
MODEL_ID = re.compile(r"claude-[a-z0-9]+(?:[.-][a-z0-9]+)*")
MAX_MODEL_ID = 100
_HINT_MIN_KEY = 20  # a shorter key would be mostly revealed by prefix + last four
_VENDOR_PREFIX = "sk-ant-"

VerdictReason = Literal[
    "passed",
    "readiness_not_required",
    "disabled",
    "no_report",
    "other_mode",
    "models_changed",
    "key_rotated",
    "key_missing",
    "endpoint_changed",
    "report_failed",
]
MAX_BASE_URL = 300  # provider_configs.base_url


@dataclass(frozen=True, slots=True)
class WorkerCredentials:
    """What the worker holds now; 3.1f builds it from the worker's own settings before a model call. The
    ``api`` process holds none and passes nothing, so its verdict is advisory (no rotation check)."""

    key: str | None
    base_url: str | None


@dataclass(frozen=True, slots=True)
class ProviderVerdict:
    state: ProviderState
    reason: VerdictReason

    @property
    def ready(self) -> bool:
        return self.state == "ready"


# --- key status (the worker's view) -------------------------------------------------------------------------


def key_fingerprint(key: str) -> str:
    """The first 16 hex digits of SHA-256: detects a rotated key, cannot be turned back into it."""
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def key_hint(key: str) -> str:
    """A masked identifier (FR-128): the vendor prefix and the last four characters, ``sk-ant-…a1b2``. The
    prefix only for a key that has it (a mis-set value shows ``…`` and its last four; AG-13 3.1e1 L2)."""
    if len(key) < _HINT_MIN_KEY:
        return "…"
    prefix = _VENDOR_PREFIX if key.startswith(_VENDOR_PREFIX) else ""
    return f"{prefix}…{key[-4:]}"


def public_base_url(url: str | None) -> str | None:
    """The endpoint as stored and shown: scheme, host, port and path. User info, query and fragment are
    dropped (a gateway URL may carry a token there; backend review 3.1e1 M1); cut to the column's size."""
    if not url:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    port = f":{parts.port}" if parts.port else ""
    public = urlunsplit((parts.scheme.lower(), f"{host}{port}", parts.path, "", ""))
    return public if len(public) <= MAX_BASE_URL else public[: MAX_BASE_URL - 1] + "…"


# --- models -------------------------------------------------------------------------------------------------


def validate_model_policy(raw: Mapping[str, Any]) -> dict[str, str]:
    """``{alias: model id}``; an empty or null value drops the alias (it then uses ``CLAUDE_MODEL_*``)."""
    policy: dict[str, str] = {}
    for alias, value in raw.items():
        if alias not in MODEL_ALIASES:
            # Not echoed: a key pasted into the wrong field must not come back in the 422 (AG-13 3.1e1 L3).
            raise ValueError(f"unknown model alias; expected one of {', '.join(MODEL_ALIASES)}")
        if value is None or value == "":
            continue
        if not isinstance(value, str) or len(value) > MAX_MODEL_ID or not MODEL_ID.fullmatch(value):
            raise ValueError(f"model for {alias!r} must be a Claude model id such as claude-<name>")
        policy[alias] = value
    return policy


def _env_models(settings: Settings) -> dict[str, str]:
    # Settings fills fast/complex from default when unset.
    return {
        "default": settings.claude_model_default,
        "fast": settings.claude_model_fast or settings.claude_model_default,
        "complex": settings.claude_model_complex or settings.claude_model_default,
    }


def resolve_model(alias: str, settings: Settings, policy: Mapping[str, Any]) -> str:
    if alias not in MODEL_ALIASES:
        raise ValueError(f"unknown model alias {alias!r}")
    chosen = policy.get(alias)
    return chosen if isinstance(chosen, str) and chosen else _env_models(settings)[alias]


def effective_models(settings: Settings, policy: Mapping[str, Any]) -> dict[str, str]:
    return {alias: resolve_model(alias, settings, policy) for alias in MODEL_ALIASES}


def set_model_policy(row: ProviderConfig, policy: dict[str, str]) -> bool:
    """Store a validated policy; a change puts readiness back to ``unknown`` (ADR-032). True if changed."""
    if dict(row.model_policy or {}) == policy:
        return False
    row.model_policy = policy
    row.readiness_status = "unknown"
    return True


# --- the row ------------------------------------------------------------------------------------------------


async def ensure_config(session: AsyncSession, provider: str = PROVIDER) -> ProviderConfig:
    """The provider's row, created with its defaults when missing (idempotent, safe under concurrency)."""
    await session.execute(
        insert(ProviderConfig).values(provider=provider).on_conflict_do_nothing(index_elements=["provider"])
    )
    return (await session.scalars(select(ProviderConfig).where(ProviderConfig.provider == provider))).one()


async def record_report(
    session: AsyncSession,
    report: ReadinessReport,
    *,
    key: str | None,
    base_url: str | None,
    pricing_version: str | None,
    models: Mapping[str, str],
    settings: Settings,
    audit: AuditWriter,
    actor_id: str,
    job_id: uuid.UUID | None = None,
) -> ProviderConfig:
    """Write a report the worker produced; the caller commits. ``models`` (what was tested) is stored with
    it: a report counts only while the effective models are still those (``provider_ready``). ``job_id`` names
    the smoke run, so the API can tell when the report it asked for has landed."""
    row = await ensure_config(session, report.provider)
    await session.refresh(row, with_for_update=True)
    now = datetime.now(UTC)
    # A policy changed while the smoke ran: the report stays, but it is not about the models in effect.
    stale = effective_models(settings, row.model_policy or {}) != dict(models)
    row.llm_provider_mode = report.mode
    row.readiness_status = "unknown" if stale else ("passed" if report.ready else "failed")
    row.readiness_checked_at = report.ran_at
    row.readiness_report = {
        **report.to_dict(),
        "models": dict(models),
        "job_id": str(job_id) if job_id else None,
    }
    row.secret_status = "configured" if key else "missing"
    row.secret_hint = key_hint(key) if key else None
    row.secret_fingerprint = key_fingerprint(key) if key else None
    row.secret_checked_at = now
    row.base_url = public_base_url(base_url)
    row.pricing_version = pricing_version
    await audit.write(
        session,
        actor_type="system",
        actor_id=actor_id,
        action="provider.readiness_recorded",
        result="success",
        resource_type="provider_config",
        resource_id=str(row.id),
        metadata={
            "provider": report.provider,
            "mode": report.mode,
            "ready": report.ready,
            "failed": [check.key for check in report.checks if check.status == "failed"],
            "skipped": [check.key for check in report.checks if check.status == "skipped"],
            "secret_status": row.secret_status,
            "models": dict(models),
            "stale": stale,
            "job_id": str(job_id) if job_id else None,
        },
    )
    await session.flush()
    return row


# --- predicates (enforced by 3.1f) --------------------------------------------------------------------------


def provider_ready(
    row: ProviderConfig | None, settings: Settings, *, held: WorkerCredentials | None = None
) -> ProviderVerdict:
    """Ready only for a passed report of the current ``LLM_PROVIDER`` mode, made with the models in effect
    now, on an enabled provider. ``held`` is what the worker holds now: no key is not ready; another key
    fingerprint (rotated) or another endpoint than the report used is unknown again. The ``api`` process
    holds nothing and passes nothing: its verdict cannot see a rotation. ``LLM_REQUIRE_READINESS=false``
    (refused in production) skips the report."""
    if row is not None and not row.enabled:
        return ProviderVerdict("disabled", "disabled")
    if not settings.llm_require_readiness:
        return ProviderVerdict("ready", "readiness_not_required")
    if row is None or row.readiness_status == "unknown" or not row.readiness_report:
        return ProviderVerdict("unknown", "no_report")
    if row.llm_provider_mode != settings.llm_provider:
        return ProviderVerdict("unknown", "other_mode")
    if row.readiness_report.get("models") != effective_models(settings, row.model_policy or {}):
        return ProviderVerdict("unknown", "models_changed")
    if held is not None:
        if row.llm_provider_mode == "anthropic" and not held.key:
            return ProviderVerdict("not_ready", "key_missing")
        if held.key and row.secret_fingerprint != key_fingerprint(held.key):
            return ProviderVerdict("unknown", "key_rotated")
        if row.base_url != public_base_url(held.base_url):
            return ProviderVerdict("unknown", "endpoint_changed")
    if row.readiness_status != "passed":
        return ProviderVerdict("not_ready", "report_failed")
    return ProviderVerdict("ready", "passed")


async def current_verdict(
    session: AsyncSession, settings: Settings, provider: str = PROVIDER
) -> ProviderVerdict:
    """``provider_ready`` for the stored row, read only (the api process: no key, no rotation check)."""
    row = (
        await session.scalars(select(ProviderConfig).where(ProviderConfig.provider == provider))
    ).one_or_none()
    return provider_ready(row, settings)


def provider_calls_allowed(project: Project) -> bool:
    """The per-project kill switch (FR-158, ADR-011): absent means on. Anything but ``true`` stops calls, so a
    malformed value fails closed."""
    return (project.settings or {}).get(KILL_SWITCH_KEY, True) is True
