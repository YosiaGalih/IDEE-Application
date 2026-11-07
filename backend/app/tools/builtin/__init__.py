"""The builtin tools of the ToolGateway (WP-3.1d2a). ``builtin_specs()`` is what production registers; the
catalog's ``implemented`` flag names exactly these (tests/unit/test_tool_catalog.py)."""

from __future__ import annotations

from app.tools.base import ToolSpec
from app.tools.builtin import git, repo, search, security


def builtin_specs() -> list[ToolSpec]:
    return [*repo.specs(), *search.specs(), *git.specs(), *security.specs()]
