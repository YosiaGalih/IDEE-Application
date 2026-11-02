"""``/projects`` (API_CONTRACTS §3, §11 #14). Thin: validate, call ``services.projects``, wrap the result."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.api.deps import AuditDep, AuthContext, CurrentUser, dependencies
from app.core.context import REQUEST_ID_HEADER
from app.core.errors import envelope, request_id_of
from app.services import projects
from app.services.projects import Actor, ProjectEnv, ProjectView

router = APIRouter(prefix="/projects", tags=["projects"])

# One path segment git accepts as a branch name; the adapter checks again before git sees it.
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


# --- request bodies ---------------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlankIn(_Strict):
    type: Literal["blank"]


class TemplateIn(_Strict):
    type: Literal["template"]
    template_id: Annotated[str, StringConstraints(min_length=1, max_length=63)]

    @field_validator("template_id")
    @classmethod
    def known_template(cls, value: str) -> str:
        if value not in {t.id for t in projects.templates()}:
            raise ValueError("unknown template")
        return value


class GitHttpsIn(_Strict):
    type: Literal["git_https"]
    url: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    branch: str | None = None


class CreateProjectIn(_Strict):
    name: Annotated[str, StringConstraints(max_length=projects.NAME_MAX * 2)]
    description: Annotated[str, StringConstraints(max_length=projects.DESCRIPTION_MAX)] | None = None
    source: Annotated[BlankIn | TemplateIn | GitHttpsIn, Field(discriminator="type")]
    default_branch: str = "main"

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not 1 <= len(cleaned) <= projects.NAME_MAX or re.search(r"[\x00-\x1f\x7f]", cleaned):
            raise ValueError(f"must be 1-{projects.NAME_MAX} characters without control characters")
        return cleaned

    @field_validator("default_branch")
    @classmethod
    def valid_branch(cls, value: str) -> str:
        if not _BRANCH.fullmatch(value) or ".." in value or value.endswith((".", ".lock")):
            raise ValueError("not a valid branch name")
        return value


class SettingsIn(_Strict):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_approval_required: bool | None = None
    auto_remediation: bool | None = None


class UpdateProjectIn(_Strict):
    name: str | None = None
    description: Annotated[str, StringConstraints(max_length=projects.DESCRIPTION_MAX)] | None = None
    settings: SettingsIn | None = None

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("name cannot be null")
        return CreateProjectIn.valid_name(value)


# --- response shapes --------------------------------------------------------------------------------------


class RepositoryOut(BaseModel):
    provider: str
    remote_url: str | None
    branch: str
    sync_state: str
    last_indexed_at: datetime | None
    error: str | None


class StatsOut(BaseModel):
    files: int | None
    last_commit: str | None
    open_tasks: int


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    status: str
    stack: dict[str, Any]
    default_branch: str
    settings: dict[str, Any]
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    repository: RepositoryOut | None
    permissions: list[str]
    stats: StatsOut | None

    @classmethod
    def of(cls, view: ProjectView) -> ProjectOut:
        project, repository, stats = view.project, view.repository, view.stats
        return cls(
            id=project.id,
            name=project.name,
            slug=project.slug,
            description=project.description,
            status=project.status,
            stack=project.stack,
            default_branch=project.default_branch,
            settings=project.settings,
            owner_id=project.owner_id,
            created_at=project.created_at,
            updated_at=project.updated_at,
            repository=None
            if repository is None
            else RepositoryOut(
                provider=repository.provider,
                remote_url=repository.remote_url,
                branch=repository.branch,
                sync_state=repository.sync_state,
                last_indexed_at=repository.last_indexed_at,
                error=repository.error,
            ),
            permissions=sorted(view.permissions),
            stats=None
            if stats is None
            else StatsOut(files=stats.files, last_commit=stats.last_commit, open_tasks=stats.open_tasks),
        )


# --- plumbing ---------------------------------------------------------------------------------------------


def _respond(
    request: Request, data: Any, *, status_code: int = 200, headers: dict[str, str] | None = None
) -> JSONResponse:
    request_id = request_id_of(request)
    # Responses carry the caller's permissions: no shared cache may keep them.
    return JSONResponse(
        envelope(request_id, status="completed", data=data),
        status_code=status_code,
        headers={REQUEST_ID_HEADER: request_id, "Cache-Control": "no-store", **(headers or {})},
    )


def project_env(request: Request, audit: AuditDep) -> ProjectEnv:
    deps = dependencies(request)
    return ProjectEnv(
        sessions=deps.sessions,
        audit=audit,
        git=deps.git,
        workspace_root=Path(request.app.state.settings.workspace_root),
    )


def actor_of(ctx: AuthContext) -> Actor:
    return Actor(ctx.user.id, ctx.user.email, ctx.user.display_name, tuple(ctx.grants))


EnvDep = Annotated[ProjectEnv, Depends(project_env)]


# --- routes (the static /templates before /{project_id}) --------------------------------------------------


@router.get("/templates")
async def list_templates(request: Request, ctx: CurrentUser) -> JSONResponse:
    del ctx  # any signed-in user
    data = [
        {"id": t.id, "name": t.name, "description": t.description, "stack": t.stack, "icon": t.icon}
        for t in projects.templates()
    ]
    return _respond(request, data)


@router.get("")
async def list_projects(
    request: Request,
    ctx: CurrentUser,
    env: EnvDep,
    status: Literal["active", "archived", "all"] = "active",
    q: Annotated[str | None, Query(max_length=120)] = None,
) -> JSONResponse:
    views = await projects.list_projects(env, actor_of(ctx), status=status, q=q or None)
    return _respond(request, [ProjectOut.of(view).model_dump(mode="json") for view in views])


@router.post("")
async def create_project(
    request: Request, body: CreateProjectIn, ctx: CurrentUser, env: EnvDep
) -> JSONResponse:
    source: projects.Source
    if isinstance(body.source, TemplateIn):
        source = projects.TemplateSource(body.source.template_id)
    elif isinstance(body.source, GitHttpsIn):
        source = projects.RemoteSource(body.source.url)
    else:
        source = projects.BlankSource()
    view = await projects.create_project(
        env,
        actor_of(ctx),
        name=body.name,
        description=body.description,
        source=source,
        default_branch=body.default_branch,
    )
    data = ProjectOut.of(view).model_dump(mode="json")
    return _respond(request, data, status_code=201, headers={"Location": f"/api/v1/projects/{data['id']}"})


@router.get("/{project_id}")
async def get_project(request: Request, project_id: uuid.UUID, ctx: CurrentUser, env: EnvDep) -> JSONResponse:
    view = await projects.get_project(env, actor_of(ctx), project_id)
    return _respond(request, ProjectOut.of(view).model_dump(mode="json"))


@router.patch("/{project_id}")
async def update_project(
    request: Request, project_id: uuid.UUID, body: UpdateProjectIn, ctx: CurrentUser, env: EnvDep
) -> JSONResponse:
    settings = body.settings.model_dump(exclude_none=True) if body.settings is not None else None
    changes = projects.ProjectChanges(
        fields=frozenset(body.model_fields_set),
        name=body.name,
        description=body.description,
        settings=settings,
    )
    view = await projects.update_project(env, actor_of(ctx), project_id, changes)
    return _respond(request, ProjectOut.of(view).model_dump(mode="json"))


@router.post("/{project_id}/archive")
async def archive_project(
    request: Request, project_id: uuid.UUID, ctx: CurrentUser, env: EnvDep
) -> JSONResponse:
    view = await projects.set_archived(env, actor_of(ctx), project_id, archived=True)
    return _respond(request, ProjectOut.of(view).model_dump(mode="json"))


@router.post("/{project_id}/unarchive")
async def unarchive_project(
    request: Request, project_id: uuid.UUID, ctx: CurrentUser, env: EnvDep
) -> JSONResponse:
    view = await projects.set_archived(env, actor_of(ctx), project_id, archived=False)
    return _respond(request, ProjectOut.of(view).model_dump(mode="json"))


@router.get("/{project_id}/stack")
async def get_stack(request: Request, project_id: uuid.UUID, ctx: CurrentUser, env: EnvDep) -> JSONResponse:
    view = await projects.get_project(env, actor_of(ctx), project_id, with_stats=False)
    return _respond(request, view.project.stack)
