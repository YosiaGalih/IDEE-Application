"""Only ``app/services/agent_runtime.py`` writes ``agent_runs`` (plan WP-3.1f2a Task 7, Keputusan plan #19).

The pattern of tests/unit/test_usage_writer_guard.py: a module that reaches ``AgentRun`` must not build one,
nor ``insert``/``update``/``delete`` it, and no module holds raw SQL that writes ``agent_runs``. Recovery
(WP-3.1f2b) becomes the second writer.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
ALLOWED_WRITER = APP / "services" / "agent_runtime.py"
MODEL = "AgentRun"
MODULE = "app.models.agents"
WRITE_FUNCTIONS = {"insert", "update", "delete", "pg_insert"}
RAW_SQL = re.compile(r"\b(insert\s+into|update|delete\s+from)\s+agent_runs\b", re.IGNORECASE)


def _model_names(tree: ast.Module) -> set[str]:
    classes = {MODEL}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == MODULE:
            classes |= {a.asname or a.name for a in node.names if a.name == MODEL}
    return classes


def _is_model(node: ast.expr, classes: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in classes
    if isinstance(node, ast.Attribute):
        return node.attr == MODEL or (node.attr == "__table__" and _is_model(node.value, classes))
    return False


def _string_value(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}" for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string_value(node.left), _string_value(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def run_writes(source: str) -> list[str]:
    tree = ast.parse(source)
    classes = _model_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if _is_model(func, classes):
                found.append(f"line {node.lineno}: builds {MODEL}(...)")
            elif name in WRITE_FUNCTIONS and node.args and _is_model(node.args[0], classes):
                found.append(f"line {node.lineno}: {name}({MODEL})")
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
            value = _string_value(node)
            if value is not None and RAW_SQL.search(value):
                found.append(f"line {node.lineno}: raw SQL writes agent_runs")
    return found


def test_only_the_runtime_store_writes_agent_runs() -> None:
    violations = {
        str(path.relative_to(APP)): hits
        for path in APP.rglob("*.py")
        if path != ALLOWED_WRITER and (hits := run_writes(path.read_text()))
    }
    assert violations == {}


def test_guard_sees_the_allowed_writer() -> None:
    hits = run_writes(ALLOWED_WRITER.read_text())
    assert any("AgentRun(...)" in hit for hit in hits) and any("update(AgentRun)" in hit for hit in hits)


def test_guard_detects_a_second_writer() -> None:
    for source in (
        "from app.models.agents import AgentRun\nsession.add(AgentRun(task_id=t))\n",
        "from app.models.agents import AgentRun as R\nrow = R(step='x')\n",
        "from sqlalchemy import update\nfrom app.models.agents import AgentRun\nq = update(AgentRun)\n",
        "from sqlalchemy import delete\nfrom app.models.agents import AgentRun\n"
        "q = delete(AgentRun.__table__)\n",
        "Q = 'UPDATE agent_runs SET status = :s'\n",
        "Q = 'insert ' + 'into agent_runs (id) values (:id)'\n",
    ):
        assert run_writes(source), source
    assert run_writes("from app.models.agents import AgentRun\nq = select(AgentRun)\n") == []
