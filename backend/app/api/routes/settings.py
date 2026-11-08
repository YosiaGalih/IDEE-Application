"""Provider settings routes (API_CONTRACTS §9, §11 #8, §11.3; spec 3.1e Goal 5). Thin.

Every route needs the global ``settings:providers`` permission (owner, admin); the service checks it again.
The ``api`` process never holds the key: a smoke test is a worker job (Lead decision 3.1e #3).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator

from app.api.deps import AuditDep, AuthContext, CurrentActor, LimiterDep, dependencies, require_permission
from app.api.routes.projects import _respond
from app.services import provider_settings
from app.services.provider_settings import ProviderSettingsEnv, ProviderView

router = APIRouter(prefix="/settings", tags=["settings"])

ProvidersAdmin = Annotated[AuthContext, Depends(require_permission(provider_settings.PERMISSION))]


def provider_env(request: Request, audit: AuditDep, limiter: LimiterDep) -> ProviderSettingsEnv:
    deps = dependencies(request)
    return ProviderSettingsEnv(
        sessions=deps.sessions,
        audit=audit,
        settings=request.app.state.settings,
        redis=deps.redis,
        smoke_jobs=deps.smoke_jobs,
        limiter=limiter,
    )


EnvDep = Annotated[ProviderSettingsEnv, Depends(provider_env)]


class ProviderPatch(BaseModel):
    """Only these two fields: ``base_url`` and any key are refused as unknown fields (Lead decision #6)."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model_policy: dict[str, str | None] | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> ProviderPatch:
        if not self.model_fields_set:
            raise ValueError("send model_policy, enabled, or both")
        if "enabled" in self.model_fields_set and self.enabled is None:
            raise ValueError("enabled must be true or false")
        return self


def provider_out(view: ProviderView) -> dict[str, Any]:
    row = view.row
    return {
        "id": str(row.id),
        "provider": row.provider,
        "base_url": row.base_url,
        "model_policy": dict(row.model_policy or {}),
        "effective_models": view.effective_models,
        "secret_status": row.secret_status,
        "secret_hint": row.secret_hint,
        "secret_checked_at": row.secret_checked_at.isoformat() if row.secret_checked_at else None,
        "enabled": row.enabled,
        "pricing_version": row.pricing_version,
        "llm_provider_mode": row.llm_provider_mode,
        "readiness_status": row.readiness_status,
        "readiness_checked_at": row.readiness_checked_at.isoformat() if row.readiness_checked_at else None,
        "readiness_effective": {"state": view.verdict.state, "reason": view.verdict.reason},
    }


@router.get("/providers")
async def list_providers(
    request: Request, _: ProvidersAdmin, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    views = await provider_settings.list_providers(env, actor)
    return _respond(request, [provider_out(view) for view in views], meta={"total": len(views)})


@router.patch("/providers/{provider_id}")
async def patch_provider(
    request: Request,
    provider_id: uuid.UUID,
    body: ProviderPatch,
    _: ProvidersAdmin,
    actor: CurrentActor,
    env: EnvDep,
) -> JSONResponse:
    fields: dict[str, Any] = {name: getattr(body, name) for name in body.model_fields_set}
    view = await provider_settings.update_provider(env, actor, provider_id, **fields)
    return _respond(request, provider_out(view))


@router.get("/providers/{provider_id}/readiness")
async def get_readiness(
    request: Request, provider_id: uuid.UUID, _: ProvidersAdmin, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    return _respond(request, await provider_settings.get_readiness(env, actor, provider_id))


@router.post("/providers/{provider_id}/smoke-test")
async def post_smoke_test(
    request: Request, provider_id: uuid.UUID, _: ProvidersAdmin, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    outcome = await provider_settings.request_smoke(env, actor, provider_id)
    if outcome.report is None:
        return _respond(request, {"job_id": str(outcome.job_id)}, status_code=202, status="accepted")
    return _respond(request, outcome.report, meta={"job_id": str(outcome.job_id)})
