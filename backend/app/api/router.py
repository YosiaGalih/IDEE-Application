from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    agents,
    approvals,
    auth,
    budget,
    dashboard,
    git,
    health,
    project_imports,
    projects,
    repository,
    settings,
    usage,
    workspace,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(agents.router)
api_router.include_router(approvals.router)
api_router.include_router(projects.router)
api_router.include_router(project_imports.router)
api_router.include_router(workspace.router)
api_router.include_router(git.router)
api_router.include_router(repository.router)
api_router.include_router(budget.router)
api_router.include_router(settings.router)
api_router.include_router(usage.router)
