"""Workspace routes (API_CONTRACTS §3 ``GET /tree``, §4 files and patches; WP-2.1c). Thin: the service checks.

Every route has the project door (``require_project_permission``): 404 outside the scope, 403 without the
permission; the service authorises again and checks the repository and write preconditions.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import require_project_permission
from app.api.routes.projects import EnvDep, _respond
from app.services import workspace_api
from app.services.projects import Actor
from app.workspace.fs import TreeEntry

router = APIRouter(prefix="/projects", tags=["workspace"])

WorkspaceReader = Annotated[Actor, Depends(require_project_permission("workspace:read"))]


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
