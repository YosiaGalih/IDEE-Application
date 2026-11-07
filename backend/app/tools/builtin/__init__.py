"""The builtin tools of the ToolGateway (WP-3.1d2a, 3.1d2b). ``builtin_specs(symbols)`` is what production
registers; the catalog's ``implemented`` flag names exactly these (tests/unit/test_tool_catalog.py).
``repo.search_symbol`` needs the index port (``ToolPorts.symbols``, ADR-022): registered only with it."""

from __future__ import annotations

from app.tools.base import SymbolIndex, ToolSpec
from app.tools.builtin import code, commit, git, repo, search, security, symbols


def builtin_specs(index: SymbolIndex | None = None) -> list[ToolSpec]:
    found = [*repo.specs(), *search.specs(), *git.specs(), *security.specs(), *code.specs(), *commit.specs()]
    return found + (symbols.specs(index) if index is not None else [])
