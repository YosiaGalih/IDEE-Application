from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from app.agents.registry import ONLY_DEPLOYER, RUNTIME_CONTRACTS
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


def test_registry_tables_match_the_ci_validator() -> None:
    # The other tables the loader shares with CI (AG-13 I5); PINNED_PERMISSIONS: test_registry_loader.py.
    ci = ci_validator()
    assert set(ONLY_DEPLOYER) == ci.ONLY_DEPLOYER
    assert dict(RUNTIME_CONTRACTS) == ci.FALLBACK_CONTRACTS


def test_implemented_is_exactly_what_production_registers() -> None:
    # WP-3.1d2a: readiness (3.1b) must never call a tool implemented that the gateway does not register.
    from app.tools.builtin import builtin_specs

    specs = builtin_specs()
    assert {name for name, tool in TOOL_CATALOG.items() if tool.implemented} == {s.name for s in specs}
    assert all(TOOL_CATALOG[s.name].status == "mvp" and TOOL_CATALOG[s.name].risk == f"R{int(s.risk)}"
               for s in specs)  # fmt: skip


async def test_the_production_gateway_registers_every_builtin() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.audit import AuditWriter
    from app.core.redaction import Redactor
    from app.services.tool_gateway import build_tool_gateway
    from app.tools.catalog import IMPLEMENTED

    built = build_tool_gateway(sessions=async_sessionmaker(), audit=AuditWriter(Redactor()))
    assert built.registered == IMPLEMENTED


def test_submit_result_is_terminal() -> None:
    tool = TOOL_CATALOG["submit_result"]
    assert (tool.risk, tool.executor, tool.status) == ("-", "terminal", "mvp")
