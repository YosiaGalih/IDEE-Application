from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import auth, health, project_imports, projects

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(project_imports.router)
