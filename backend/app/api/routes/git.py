"""Git routes (API_CONTRACTS §4 ``/git/*``; WP-2.1c). Thin: the service checks, the adapter runs git.

Every route has the project door (``require_project_permission``); the service authorises again and checks
the repository preconditions. Branch names, refs and paths reach git only through ``GitAdapter``, which
refuses options, magic pathspecs and destructive commands (FR-117, FR-118).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.deps import require_project_permission
from app.api.routes.projects import EnvDep, _respond
from app.services import workspace_api
from app.services.projects import Actor

router = APIRouter(prefix="/projects", tags=["git"])

GitReader = Annotated[Actor, Depends(require_project_permission("git:read"))]
GitBrancher = Annotated[Actor, Depends(require_project_permission("git:branch"))]
WorkspaceWriter = Annotated[Actor, Depends(require_project_permission("workspace:write"))]


PATHS_MAX = 500
PATHS_MAX_BYTES = 128 * 1024  # git receives them as arguments: stay far below the kernel's ARG_MAX


class NewBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    from_ref: Annotated[str | None, Field(alias="from", min_length=1, max_length=255)] = None


class NewCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, Field(min_length=1, max_length=5000)]
    paths: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=4096)]] | None,
        Field(min_length=1, max_length=PATHS_MAX),
    ] = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, message: str) -> str:
        if not message.strip():
            raise ValueError("the message is blank")
        return message

    @field_validator("paths")
    @classmethod
    def _bounded(cls, paths: list[str] | None) -> list[str] | None:
        if paths is not None and sum(len(path.encode()) for path in paths) > PATHS_MAX_BYTES:
            raise ValueError(f"paths exceed {PATHS_MAX_BYTES} bytes in total")
        return paths


@router.get("/{project_id}/git/status")
async def get_status(request: Request, project_id: uuid.UUID, actor: GitReader, env: EnvDep) -> JSONResponse:
    return _respond(request, asdict(await workspace_api.git_status(env, actor, project_id)))


@router.get("/{project_id}/git/diff")
async def get_diff(
    request: Request,
    project_id: uuid.UUID,
    actor: GitReader,
    env: EnvDep,
    base: Annotated[str | None, Query(max_length=255)] = None,
    path: Annotated[str | None, Query(max_length=4096)] = None,
) -> JSONResponse:
    diff = await workspace_api.git_diff(env, actor, project_id, base=base, path=path)
    return _respond(request, asdict(diff))


@router.get("/{project_id}/git/branches")
async def get_branches(
    request: Request, project_id: uuid.UUID, actor: GitReader, env: EnvDep
) -> JSONResponse:
    branches = await workspace_api.git_branches(env, actor, project_id)
    return _respond(request, [asdict(branch) for branch in branches])


@router.get("/{project_id}/git/log")
async def get_log(
    request: Request,
    project_id: uuid.UUID,
    actor: GitReader,
    env: EnvDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> JSONResponse:
    entries = await workspace_api.git_log(env, actor, project_id, limit=limit)
    return _respond(request, [asdict(entry) for entry in entries])


@router.post("/{project_id}/git/branch")
async def post_branch(
    request: Request, project_id: uuid.UUID, body: NewBranch, actor: GitBrancher, env: EnvDep
) -> JSONResponse:
    branch = await workspace_api.create_branch(env, actor, project_id, name=body.name, from_ref=body.from_ref)
    return _respond(request, {"name": branch.name, "head": branch.head}, status_code=201)


@router.post("/{project_id}/git/commit")
async def post_commit(
    request: Request, project_id: uuid.UUID, body: NewCommit, actor: WorkspaceWriter, env: EnvDep
) -> JSONResponse:
    sha = await workspace_api.commit(env, actor, project_id, message=body.message, paths=body.paths)
    return _respond(request, {"commit": sha}, status_code=201)
