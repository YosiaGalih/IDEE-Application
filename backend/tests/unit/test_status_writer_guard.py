"""Only workflow/states.py may write tasks.status (CLAUDE.md #4, ARCHITECTURE §5.1).

A static scan of app/: a module that imports the Task model must not assign ``.status`` or pass
``status=`` to ``.values()``, and no module may contain raw SQL that updates tasks.status.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
ALLOWED_WRITER = APP / "workflow" / "states.py"
RAW_SQL = re.compile(r"update\s+tasks\s+set\b[^;]*\bstatus\b", re.IGNORECASE | re.DOTALL)


def _imports_task(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.models.tasks":
            if any(alias.name in {"Task", "*"} for alias in node.names):
                return True
    return False


def _has_status_key(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Dict) and any(
        isinstance(key, ast.Constant) and key.value == "status" for key in node.keys
    )


def _dicts_with_status(tree: ast.Module) -> set[str]:
    """Names bound to a dict literal that has a "status" key (``values = {"status": ...}``)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _has_status_key(node.value):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif (
            isinstance(node, ast.AnnAssign)
            and _has_status_key(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _values_sets_status(call: ast.Call, status_dicts: set[str]) -> bool:
    if any(k.arg == "status" for k in call.keywords):
        return True
    splats = [k.value for k in call.keywords if k.arg is None] + list(call.args)
    return any(_has_status_key(v) or (isinstance(v, ast.Name) and v.id in status_dicts) for v in splats)


def status_writes(source: str) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    uses_task = _imports_task(tree)
    status_dicts = _dicts_with_status(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and RAW_SQL.search(node.value):
            found.append(f"line {node.lineno}: raw SQL updates tasks.status")
        if not uses_task:
            continue
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "status":
                found.append(f"line {node.lineno}: assigns .status")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "values":
                if _values_sets_status(node, status_dicts):
                    found.append(f"line {node.lineno}: .values(status=...)")
            if isinstance(func, ast.Name) and func.id == "setattr" and len(node.args) >= 2:
                name = node.args[1]
                if isinstance(name, ast.Constant) and name.value == "status":
                    found.append(f"line {node.lineno}: setattr(..., 'status', ...)")
    return found


def test_only_states_module_writes_task_status() -> None:
    violations = {
        str(path.relative_to(APP)): hits
        for path in APP.rglob("*.py")
        if path != ALLOWED_WRITER and (hits := status_writes(path.read_text()))
    }
    assert violations == {}


def test_guard_sees_the_allowed_writer() -> None:
    # Sanity check that the scan would notice the real writer if it were not exempt.
    assert status_writes(ALLOWED_WRITER.read_text())


def test_guard_detects_attribute_write() -> None:
    source = "from app.models.tasks import Task\n\ndef f(task: Task) -> None:\n    task.status = 'running'\n"
    assert status_writes(source) == ["line 4: assigns .status"]


def test_guard_detects_update_values() -> None:
    source = (
        "from sqlalchemy import update\nfrom app.models.tasks import Task\n\n"
        "stmt = update(Task).values(status='completed')\n"
    )
    assert status_writes(source) == ["line 4: .values(status=...)"]


def test_guard_detects_values_from_a_dict() -> None:
    source = (
        "from sqlalchemy import update\nfrom app.models.tasks import Task\n\n"
        "changes = {'status': 'completed'}\nstmt = update(Task).values(**changes)\n"
        "other = update(Task).values({'status': 'failed'})\n"
    )
    assert status_writes(source) == ["line 5: .values(status=...)", "line 6: .values(status=...)"]


def test_guard_detects_raw_sql() -> None:
    source = "QUERY = 'UPDATE tasks SET status = :s WHERE id = :id'\n"
    assert status_writes(source) == ["line 1: raw SQL updates tasks.status"]


def test_guard_detects_setattr() -> None:
    source = (
        "from app.models.tasks import Task\n\ndef f(task: Task) -> None:\n    setattr(task, 'status', 'x')\n"
    )
    assert status_writes(source) == ["line 4: setattr(..., 'status', ...)"]


def test_user_status_writes_elsewhere_are_not_flagged() -> None:
    source = (
        "from app.models.identity import User\n\ndef f(user: User) -> None:\n    user.status = 'disabled'\n"
    )
    assert status_writes(source) == []
