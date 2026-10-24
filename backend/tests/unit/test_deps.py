"""``require_permission`` only guards global-only permissions (WP-1.1c security pre-review, LOW T6)."""

from __future__ import annotations

import pytest

from app.api.deps import require_permission


def test_require_permission_rejects_project_permission() -> None:
    # A project permission without a project id would grant it from any global role.
    with pytest.raises(ValueError, match="project"):
        require_permission("project:write")


def test_require_permission_rejects_unknown_permission() -> None:
    with pytest.raises(ValueError):
        require_permission("everything:please")


def test_require_permission_accepts_global_only() -> None:
    assert callable(require_permission("audit:read"))
