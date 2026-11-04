"""Workspace routes (API_CONTRACTS §3 ``GET /tree``, §4 files and patches; WP-2.1c). Thin: the service checks.

Every route has the project door (``require_project_permission``): 404 outside the scope, 403 without the
permission; the service authorises again and checks the repository and write preconditions.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_project_permission
from app.api.routes.projects import EnvDep, _respond
from app.core.errors import AppError
from app.services import workspace_api
from app.services.projects import Actor
from app.workspace.fs import FileChange, TreeEntry

router = APIRouter(prefix="/projects", tags=["workspace"])

WorkspaceReader = Annotated[Actor, Depends(require_project_permission("workspace:read"))]
WorkspaceWriter = Annotated[Actor, Depends(require_project_permission("workspace:write"))]

SHA256 = r"^[0-9a-f]{64}$"


class PutFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    # Required, null allowed: a client that forgets the version never overwrites by accident.
    expected_sha256: Annotated[str | None, Field(pattern=SHA256)]


class CreatePath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(min_length=1, max_length=4096)]
    type: Literal["file", "dir"]
    content: str | None = None


class Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diff: Annotated[str, Field(min_length=1)]
    target: str = "main"


def _entry(entry: TreeEntry) -> dict[str, Any]:
    data: dict[str, Any] = {"path": entry.path, "name": entry.name, "type": entry.type, "size": entry.size}
    if entry.children is not None:
        data["children"] = [_entry(child) for child in entry.children]
    return data


@router.get("/{project_id}/tree")
async def get_tree(
    request: Request,
    project_id: uuid.UUID,
    actor: WorkspaceReader,
    env: EnvDep,
    path: Annotated[str, Query(max_length=4096)] = ".",
    depth: Annotated[int, Query(ge=1, le=10)] = 3,
) -> JSONResponse:
    result = await workspace_api.tree(env, actor, project_id, path=path, depth=depth)
    return _respond(
        request, [_entry(entry) for entry in result.entries], meta={"truncated": result.truncated}
    )


@router.get("/{project_id}/files/{path:path}")
async def get_file(
    request: Request,
    project_id: uuid.UUID,
    path: str,
    actor: WorkspaceReader,
    env: EnvDep,
    ref: Annotated[str | None, Query(max_length=64)] = None,
) -> JSONResponse:
    view = await workspace_api.read_file(env, actor, project_id, path, ref=ref)
    return _respond(request, asdict(view))


def _change(change: FileChange) -> dict[str, Any]:
    """API_CONTRACTS §4 ``FileChange``."""
    return {"path": change.path, "change": change.change, "sha256": change.sha256}


@router.put("/{project_id}/files/{path:path}")
async def put_file(
    request: Request,
    project_id: uuid.UUID,
    path: str,
    body: PutFile,
    actor: WorkspaceWriter,
    env: EnvDep,
) -> JSONResponse:
    change = await workspace_api.write_file(
        env, actor, project_id, path, body.content, expected_sha256=body.expected_sha256
    )
    return _respond(request, {"path": change.path, "sha256": change.sha256})


@router.post("/{project_id}/files")
async def post_file(
    request: Request, project_id: uuid.UUID, body: CreatePath, actor: WorkspaceWriter, env: EnvDep
) -> JSONResponse:
    if body.type == "dir" and body.content is not None:
        raise AppError(
            "VALIDATION_ERROR",
            "Input did not pass validation",
            http_status=422,
            details=[{"loc": ["body", "content"], "msg": "a folder has no content"}],
        )
    created = await workspace_api.create(
        env, actor, project_id, body.path, kind=body.type, content=body.content
    )
    return _respond(request, {"path": created}, status_code=201)


@router.delete("/{project_id}/files/{path:path}")
async def delete_file(
    request: Request,
    project_id: uuid.UUID,
    path: str,
    actor: WorkspaceWriter,
    env: EnvDep,
    expected_sha256: Annotated[str, Query(pattern=SHA256)],
) -> JSONResponse:
    await workspace_api.delete_file(env, actor, project_id, path, expected_sha256=expected_sha256)
    return _respond(request, {})


@router.post("/{project_id}/patches")
async def post_patch(
    request: Request, project_id: uuid.UUID, body: Patch, actor: WorkspaceWriter, env: EnvDep
) -> JSONResponse:
    changes = await workspace_api.apply_patch(env, actor, project_id, body.diff, target=body.target)
    return _respond(request, {"applied": [_change(change) for change in changes], "rejected": []})
