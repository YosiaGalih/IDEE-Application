"""Import every model module so ``Base.metadata`` is complete (used by Alembic)."""

from app.models import audit
from app.models.base import Base

__all__ = ["Base", "audit"]
