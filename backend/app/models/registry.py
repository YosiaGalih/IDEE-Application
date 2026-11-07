"""Import every model module so ``Base.metadata`` is complete (used by Alembic)."""

from app.models import agents, approvals, audit, identity, projects, tasks, tools
from app.models.base import Base

__all__ = ["Base", "agents", "approvals", "audit", "identity", "projects", "tasks", "tools"]
