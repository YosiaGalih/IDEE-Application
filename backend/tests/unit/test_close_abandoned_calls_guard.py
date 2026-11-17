"""Only ``app/services/run_recovery.py`` calls ``close_abandoned_calls`` (WP-3.1f2b1 AG-13 INFO-3).

The function closes ``running`` tool calls as ``error``/``outcome_unknown`` without asking whether their
worker is alive: the caller's predicates (lease, run state) and row locks are what make that safe. The pattern
of tests/unit/test_agent_run_writer_guard.py: a module other than the recovery may not name the function at
all (call, import, attribute, string), and the gateway that defines it never calls it.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
DEFINER = APP / "tools" / "gateway.py"
CALLER = APP / "services" / "run_recovery.py"
NAME = "close_abandoned_calls"


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


def test_only_the_run_recovery_calls_close_abandoned_calls() -> None:
    violations = {
        str(path.relative_to(APP)): hits
        for path in APP.rglob("*.py")
        if path not in (DEFINER, CALLER) and (hits := references(path.read_text()))
    }
    assert violations == {}
    assert calls(DEFINER.read_text()) == []  # the gateway defines it and never calls it itself


def test_guard_sees_the_allowed_caller() -> None:
    assert calls(CALLER.read_text())


def test_guard_detects_another_caller() -> None:
    for source in (
        "from app.tools.gateway import close_abandoned_calls\n",
        "from app.tools import gateway\nawait gateway.close_abandoned_calls(s, a, c, project_id=p)\n",
        "import app.tools.gateway as g\nf = g.close_abandoned_calls\n",
        "f = getattr(gateway, 'close_abandoned_calls')\n",
        "close = close_abandoned_calls\n",
    ):
        assert references(source), source
    assert references('def close_abandoned_calls():\n    """close_abandoned_calls closes."""\n') == []
