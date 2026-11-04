"""Repository index and search routes (API_CONTRACTS §3 ``POST /index``, ``GET /search``; WP-2.1d). Thin.

Both have the project door (``require_project_permission``): 404 outside the scope, 403 without the
permission; the service authorises again and checks the repository preconditions (``open_repo``).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.deps import dependencies, require_project_permission
from app.api.routes.projects import EnvDep, _respond
from app.services import repo_index
from app.services.projects import Actor, ProjectEnv

router = APIRouter(prefix="/projects", tags=["repository"])

WorkspaceReader = Annotated[Actor, Depends(require_project_permission("workspace:read"))]
WorkspaceWriter = Annotated[Actor, Depends(require_project_permission("workspace:write"))]

# No control characters: git takes the pattern as one argument line (spec Goal 7).
_NO_CONTROL = r"^[^\x00-\x1f\x7f]*$"


def index_env(request: Request, env: ProjectEnv) -> repo_index.IndexEnv:
    deps = dependencies(request)
    return repo_index.IndexEnv(projects=env, redis=deps.redis, jobs=deps.jobs)


@router.post("/{project_id}/index")
async def post_index(
    request: Request, project_id: uuid.UUID, actor: WorkspaceWriter, env: EnvDep
) -> JSONResponse:
    queued = await repo_index.index_by_user(index_env(request, env), actor, project_id)
    return _respond(request, {"job_id": str(queued.job_id)}, status_code=202, meta={"reused": queued.reused})


@router.get("/{project_id}/search")
async def get_search(
    request: Request,
    project_id: uuid.UUID,
    actor: WorkspaceReader,
    env: EnvDep,
    q: Annotated[str, Query(min_length=1, max_length=500, pattern=_NO_CONTROL)],
    mode: Literal["text", "symbol"] = "text",
    glob: Annotated[str | None, Query(min_length=1, max_length=256, pattern=_NO_CONTROL)] = None,
    regex: bool = False,
    ignore_case: bool = True,
    kind: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=repo_index.SEARCH_LIMIT_MAX)] = repo_index.SEARCH_LIMIT_MAX,
) -> JSONResponse:
    result = await repo_index.search(
        env,
        actor,
        project_id,
        q=q,
        mode=mode,
        glob=glob,
        regex=regex,
        ignore_case=ignore_case,
        kind=kind,
        limit=limit,
    )
    hits = [{key: value for key, value in asdict(hit).items() if value is not None} for hit in result.hits]
    meta: dict[str, object] = {"truncated": result.truncated}
    if mode == "symbol":
        meta["index_commit"] = result.index_commit
        meta["indexed_at"] = result.indexed_at.isoformat() if result.indexed_at is not None else None
    return _respond(request, hits, meta=meta)
