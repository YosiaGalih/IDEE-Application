"""Only ``app/services/agent_runtime.py`` and ``app/services/run_recovery.py`` call ``close_run_row`` (spec
WP-3.2 §16.3 Keputusan Lead #67, §16.3a #6).

``close_run_row`` ends an ``agent_runs`` row that is still ``running`` for whoever calls it. What makes that
safe is each caller's own context: the runtime ends the run it started (``AgentRunWriter.finish``, and
``fail`` for that id, task and agent only); the run recovery ends a run whose lease is dead or that is stale,
under its task's row lock. The pattern of tests/unit/test_close_abandoned_calls_guard.py: a module other than
the two callers may not name the function at all (call, import, attribute, string), and the module that
defines it never calls it.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
DEFINER = APP / "services" / "agent_run_rows.py"
CALLERS = {APP / "services" / "agent_runtime.py", APP / "services" / "run_recovery.py"}
NAME = "close_run_row"


def _docstrings(tree: ast.Module) -> set[int]:
    """The ``id`` of every docstring node: prose may name the function."""
    owners = [
        tree,
        *(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)),
    ]
    return {
        id(owner.body[0].value)
        for owner in owners
        if owner.body
        and isinstance(owner.body[0], ast.Expr)
        and isinstance(owner.body[0].value, ast.Constant)
        and isinstance(owner.body[0].value.value, str)
    }


def references(source: str) -> list[str]:
    """Every use of the name except its own ``def`` and docstrings."""
    tree = ast.parse(source)
    prose = _docstrings(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == NAME:
            found.append(f"line {node.lineno}: name")
        elif isinstance(node, ast.Attribute) and node.attr == NAME:
            found.append(f"line {node.lineno}: attribute")
        elif isinstance(node, ast.ImportFrom) and any(alias.name == NAME for alias in node.names):
            found.append(f"line {node.lineno}: import")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and NAME in node.value
            and id(node) not in prose
        ):
            found.append(f"line {node.lineno}: string")
    return found


def calls(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == NAME)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == NAME)
        )
    ]


def test_only_the_run_store_and_the_run_recovery_call_close_run_row() -> None:
    violations = {
        str(path.relative_to(APP)): hits
        for path in APP.rglob("*.py")
        if path != DEFINER and path not in CALLERS and (hits := references(path.read_text()))
    }
    assert violations == {}
    assert calls(DEFINER.read_text()) == []  # it defines the closer and never calls it itself


def test_guard_sees_the_allowed_callers() -> None:
    for caller in CALLERS:
        assert calls(caller.read_text()), caller.name


def test_guard_detects_another_caller() -> None:
    for source in (
        "from app.services.agent_run_rows import close_run_row\n",
        "from app.services import agent_run_rows\nawait agent_run_rows.close_run_row(s, b, r, i)\n",
        "import app.services.agent_run_rows as rows\nf = rows.close_run_row\n",
        "f = getattr(agent_run_rows, 'close_run_row')\n",
        "close = close_run_row\n",
    ):
        assert references(source), source
    assert references('async def close_run_row():\n    """close_run_row closes."""\n') == []
