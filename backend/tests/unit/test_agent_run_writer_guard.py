"""Two modules write ``agent_runs``, one statement each (plan WP-3.1f2a Task 7, Keputusan plan #19; spec
WP-3.2 §16.3 Keputusan Lead #67, §16.3a #6):

- ``app/services/agent_runtime.py`` creates the row: ``AgentRunWriter.start``'s ``pg_insert(AgentRun)``, and
  no UPDATE;
- ``app/services/agent_run_rows.py`` closes it: ``close_run_row``'s ``update(AgentRun)``, and nothing else.

``AgentRunWriter.finish``/``fail`` and the run recovery (``app/services/run_recovery.py``, which now has none)
close a row only through ``close_run_row`` (tests/unit/test_close_run_row_guard.py). The pattern of
tests/unit/test_usage_writer_guard.py: a module that reaches ``AgentRun`` must not build one, nor
``insert``/``update``/``delete`` it, and no module holds raw SQL that writes ``agent_runs``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
ROW_CREATOR = APP / "services" / "agent_runtime.py"
ROW_CLOSER = APP / "services" / "agent_run_rows.py"
RECOVERY = APP / "services" / "run_recovery.py"
ALLOWED_WRITERS = {ROW_CREATOR, ROW_CLOSER}
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


def test_only_the_row_creator_and_the_row_closer_write_agent_runs() -> None:
    violations = {
        str(path.relative_to(APP)): hits
        for path in APP.rglob("*.py")
        if path not in ALLOWED_WRITERS and (hits := run_writes(path.read_text()))
    }
    assert violations == {}


def _kinds(hits: list[str]) -> list[str]:
    return [hit.split(": ", 1)[1] for hit in hits]


def test_the_runtime_store_only_creates_rows() -> None:
    assert _kinds(run_writes(ROW_CREATOR.read_text())) == [f"pg_insert({MODEL})"]  # start; no UPDATE


def test_the_row_closer_only_updates() -> None:
    assert _kinds(run_writes(ROW_CLOSER.read_text())) == [f"update({MODEL})"]  # close_run_row, once


def test_the_run_recovery_writes_no_row_itself() -> None:
    assert RECOVERY.exists() and run_writes(RECOVERY.read_text()) == []  # it closes through close_run_row


def test_guard_detects_a_second_writer() -> None:
    for source in (
        "from app.models.agents import AgentRun\nsession.add(AgentRun(task_id=t))\n",
        "from app.models.agents import AgentRun as R\nrow = R(step='x')\n",
        "from sqlalchemy import update\nfrom app.models.agents import AgentRun\nq = update(AgentRun)\n",
        "from sqlalchemy import delete\nfrom app.models.agents import AgentRun\n"
        "q = delete(AgentRun.__table__)\n",
        "from sqlalchemy.dialects.postgresql import insert as pg_insert\n"
        "from app.models.agents import AgentRun\nq = pg_insert(AgentRun)\n",
        "Q = 'UPDATE agent_runs SET status = :s'\n",
        "Q = 'insert ' + 'into agent_runs (id) values (:id)'\n",
    ):
        assert run_writes(source), source
    assert run_writes("from app.models.agents import AgentRun\nq = select(AgentRun)\n") == []
