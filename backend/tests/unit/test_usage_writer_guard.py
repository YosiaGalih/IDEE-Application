"""Only ``app/services/usage.py`` writes the usage ledger (plan WP-3.1f1 Global Constraints, Keputusan plan
#13).

import-linter cannot say this: BudgetGuard and the dashboard must read ``app.models.usage``. A static scan of
app/: a module that reaches ``UsageRecord`` must not build one, nor ``insert``/``update``/``delete`` it, and
no module may hold raw SQL that writes ``usage_records``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
ALLOWED_WRITER = APP / "services" / "usage.py"
MODEL = "UsageRecord"
WRITE_FUNCTIONS = {"insert", "update", "delete", "pg_insert"}
RAW_SQL = re.compile(r"\b(insert\s+into|update|delete\s+from)\s+usage_records\b", re.IGNORECASE)


def _model_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound to the UsageRecord class, and names bound to the app.models.usage module."""
    classes: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.models.usage":
                classes |= {a.asname or a.name for a in node.names if a.name in {MODEL, "*"}}
            if node.module == "app.models":
                modules |= {a.asname or a.name for a in node.names if a.name == "usage"}
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "app.models.usage"}
    classes.discard("*")
    return classes | {MODEL}, modules


def _is_model(node: ast.expr, classes: set[str]) -> bool:
    """``UsageRecord``, an alias of it, ``<module>.UsageRecord`` or ``UsageRecord.__table__``."""
    if isinstance(node, ast.Name):
        return node.id in classes
    if isinstance(node, ast.Attribute):
        return node.attr == MODEL or (node.attr == "__table__" and _is_model(node.value, classes))
    return False


def _function_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


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


def ledger_writes(source: str) -> list[str]:
    tree = ast.parse(source)
    classes, _ = _model_names(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _function_name(node.func)
            if _is_model(node.func, classes):
                found.append(f"line {node.lineno}: builds {MODEL}(...)")
            elif name in WRITE_FUNCTIONS and node.args and _is_model(node.args[0], classes):
                found.append(f"line {node.lineno}: {name}({MODEL})")
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
            parent = parents.get(node)
            nested = isinstance(parent, ast.JoinedStr) or (
                isinstance(parent, ast.BinOp) and _string_value(parent) is not None
            )
            value = _string_value(node)
            if value is not None and not nested and RAW_SQL.search(value):
                found.append(f"line {node.lineno}: raw SQL writes usage_records")
    return found


def test_only_the_usage_service_writes_the_ledger() -> None:
    violations = {
        str(path.relative_to(APP)): hits
        for path in APP.rglob("*.py")
        if path != ALLOWED_WRITER and (hits := ledger_writes(path.read_text()))
    }
    assert violations == {}


def test_guard_sees_the_allowed_writer() -> None:
    assert ledger_writes(ALLOWED_WRITER.read_text())


def test_guard_detects_a_second_writer() -> None:
    sources = [
        "from app.models.usage import UsageRecord\nsession.add(UsageRecord(project_id=p))\n",
        "from app.models.usage import UsageRecord as U\nrow = U(attempt=1)\n",
        "from app.models import usage\nrow = usage.UsageRecord(attempt=1)\n",
        (
            "import app.models.usage as m\nfrom sqlalchemy import update\n"
            "q = update(m.UsageRecord).values(x=1)\n"
        ),
        "from sqlalchemy import insert\nfrom app.models.usage import UsageRecord\nq = insert(UsageRecord)\n",
        (
            "from sqlalchemy.dialects.postgresql import insert as pg_insert\n"
            "from app.models.usage import UsageRecord\nq = pg_insert(UsageRecord.__table__)\n"
        ),
        "from sqlalchemy import delete\nfrom app.models.usage import UsageRecord\nq = delete(UsageRecord)\n",
        "Q = 'INSERT INTO usage_records (id) VALUES (:id)'\n",
        "Q = 'UPDATE usage_records SET estimated_cost = 0'\n",
        "T = 'usage_records'\nQ = f'DELETE FROM usage_records WHERE id = {T}'\n",
        "Q = 'UPDATE ' + 'usage_records SET model = :m'\n",
    ]
    for source in sources:
        assert ledger_writes(source), source


def test_reads_are_not_flagged() -> None:
    source = (
        "from sqlalchemy import func, select\nfrom app.models.usage import UsageRecord\n\n"
        "q = select(func.sum(UsageRecord.estimated_cost)).where(UsageRecord.task_id == t)\n"
        "r = 'SELECT * FROM usage_records'\n"
    )
    assert ledger_writes(source) == []
