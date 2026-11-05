from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from app.tools.catalog import CITATION_SOURCE_TOOLS, R3_OWNERS, TOOL_CATALOG

VALIDATOR = Path(__file__).resolve().parents[3] / "scripts" / "validate_registry.py"


def ci_validator() -> ModuleType:
    """The CI validator, loaded from its path (the backend never imports scripts/ at runtime)."""
    spec = importlib.util.spec_from_file_location("validate_registry_ci", VALIDATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses/NamedTuple resolve their module by name
    spec.loader.exec_module(module)
    return module


def test_catalog_matches_the_ci_validator() -> None:
    ci = ci_validator()
    assert set(TOOL_CATALOG) == set(ci.TOOL_CATALOG)
    for name, tool in TOOL_CATALOG.items():
        theirs = ci.TOOL_CATALOG[name]
        assert (tool.risk, tool.executor, tool.preconditions, tool.status) == (
            theirs.risk,
            theirs.executor,
            theirs.pre,
            theirs.status,
        ), name
    assert {name: set(owners) for name, owners in R3_OWNERS.items()} == ci.R3_OWNERS
    assert set(CITATION_SOURCE_TOOLS) == ci.CITATION_SOURCE_TOOLS


def test_nothing_is_implemented_yet() -> None:
    # The builtin tools arrive with the ToolGateway (WP-3.1d); until then no agent may be called ready.
    assert [name for name, tool in TOOL_CATALOG.items() if tool.implemented] == []


def test_submit_result_is_terminal() -> None:
    tool = TOOL_CATALOG["submit_result"]
    assert (tool.risk, tool.executor, tool.status) == ("-", "terminal", "mvp")
