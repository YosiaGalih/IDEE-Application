"""Provider settings routes (API_CONTRACTS §9, §11 #8, §11.3; spec 3.1e Goal 5). Thin.

Every route needs the global ``settings:providers`` permission (owner, admin); the service checks it again.
The ``api`` process never holds the key: a smoke test is a worker job (Lead decision 3.1e #3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator

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

    model_policy: dict[str, StrictStr | None] | None = None
    enabled: StrictBool | None = None  # "yes" or 1 is not a boolean (backend review 3.1e1 N7)

    @model_validator(mode="after")
    def _something_to_change(self) -> ProviderPatch:
        if not self.model_fields_set:
            raise ValueError("send model_policy, enabled, or both")
        if "enabled" in self.model_fields_set and self.enabled is None:
            raise ValueError("enabled must be true or false")
        return self


class VerdictOut(BaseModel):
    state: str
    reason: str


class ProviderOut(BaseModel):
    """API §9 ``GET /settings/providers`` row. Key status is what the worker last reported (ADR-073)."""

    model_config = ConfigDict(protected_namespaces=())

    id: str
    provider: str
    base_url: str | None
    model_policy: dict[str, str]
    effective_models: dict[str, str]
    secret_status: str
    secret_hint: str | None
    secret_checked_at: datetime | None
    enabled: bool
    pricing_version: str | None
    llm_provider_mode: str | None
    readiness_status: str
    readiness_checked_at: datetime | None
    readiness_effective: VerdictOut

    @classmethod
    def of(cls, view: ProviderView) -> ProviderOut:
        row = view.row
        return cls(
            id=str(row.id),
            provider=row.provider,
            base_url=row.base_url,
            model_policy=dict(row.model_policy or {}),
            effective_models=view.effective_models,
            secret_status=row.secret_status,
            secret_hint=row.secret_hint,
            secret_checked_at=row.secret_checked_at,
            enabled=row.enabled,
            pricing_version=row.pricing_version,
            llm_provider_mode=row.llm_provider_mode,
            readiness_status=row.readiness_status,
            readiness_checked_at=row.readiness_checked_at,
            readiness_effective=VerdictOut(**view.verdict.to_dict()),
        )


class CheckOut(BaseModel):
    key: str
    label: str
    status: str
    detail: str


class ReportOut(BaseModel):
    """API §11 #8: the gate's own result (``ready``), as the worker recorded it."""

    provider: str
    mode: str
    ready: bool
    checks: list[CheckOut]
    ran_at: str


class ReadinessOut(BaseModel):
    """API §11.3. ``report_ready`` is the gate's result; ``effective`` whether it counts now."""

    provider: str
    readiness_status: str
    checked_at: str | None
    mode: str | None
    report_ready: bool
    checks: list[CheckOut]
    models: dict[str, str] | None
    job_id: str | None
    running_job_id: str | None
    effective: VerdictOut


def _out(model: BaseModel) -> Any:
    return model.model_dump(mode="json")


@router.get("/providers")
async def list_providers(
    request: Request, _: ProvidersAdmin, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    views = await provider_settings.list_providers(env, actor)
    return _respond(request, [_out(ProviderOut.of(view)) for view in views], meta={"total": len(views)})


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
    return _respond(request, _out(ProviderOut.of(view)))


@router.get("/providers/{provider_id}/readiness")
async def get_readiness(
    request: Request, provider_id: uuid.UUID, _: ProvidersAdmin, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    readiness = await provider_settings.get_readiness(env, actor, provider_id)
    return _respond(request, _out(ReadinessOut.model_validate(readiness)))


@router.post("/providers/{provider_id}/smoke-test")
async def post_smoke_test(
    request: Request, provider_id: uuid.UUID, _: ProvidersAdmin, actor: CurrentActor, env: EnvDep
) -> JSONResponse:
    outcome = await provider_settings.request_smoke(env, actor, provider_id)
    if outcome.report is None or outcome.effective is None:
        return _respond(request, {"job_id": str(outcome.job_id)}, status_code=202, status="accepted")
    meta = {"job_id": str(outcome.job_id), "effective": outcome.effective.to_dict()}
    return _respond(request, _out(ReportOut.model_validate(outcome.report)), meta=meta)
