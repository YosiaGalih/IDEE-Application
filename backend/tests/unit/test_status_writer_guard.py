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
    """Any way of reaching the Task model: from-import, module import, or package attribute."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.models.tasks":
                return True
            if node.module == "app.models" and any(alias.name == "tasks" for alias in node.names):
                return True
        if isinstance(node, ast.Import) and any(alias.name == "app.models.tasks" for alias in node.names):
            return True
    return False


def _string_value(node: ast.expr) -> str | None:
    """Text of a string constant, f-string (placeholders as {}), or a + concatenation of those."""
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


def _is_raw_status_update(node: ast.expr) -> bool:
    if not isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
        return False
    value = _string_value(node)
    if value is None:
        return False
    # A dynamic column ("{}") after UPDATE tasks SET could be status too.
    return bool(RAW_SQL.search(value) or re.search(r"update\s+tasks\s+set\s+\{\}", value, re.IGNORECASE))


def _is_status_key(key: ast.expr | None) -> bool:
    return (isinstance(key, ast.Constant) and key.value == "status") or (
        isinstance(key, ast.Attribute) and key.attr == "status"
    )


def _has_status_key(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Dict) and any(_is_status_key(key) for key in node.keys)


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


def _inside_string_expression(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Report a concatenation or f-string once, at its outermost node, not again for each part."""
    parent = parents.get(node)
    return isinstance(parent, ast.JoinedStr) or (
        isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Add) and _string_value(parent) is not None
    )


def status_writes(source: str) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    uses_task = _imports_task(tree)
    status_dicts = _dicts_with_status(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.expr)
            and _is_raw_status_update(node)
            and not _inside_string_expression(node, parents)
        ):
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
            if (
                isinstance(func, ast.Name)
                and func.id == "Task"
                and any(k.arg == "status" for k in node.keywords)
            ):
                found.append(f"line {node.lineno}: Task(status=...)")
            if isinstance(func, ast.Name) and func.id == "set_committed_value" and len(node.args) >= 2:
                name = node.args[1]
                if isinstance(name, ast.Constant) and name.value == "status":
                    found.append(f"line {node.lineno}: set_committed_value(..., 'status', ...)")
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


def test_guard_detects_other_import_forms() -> None:
    sources = [
        "from app.models import tasks\n\ndef f(t: tasks.Task) -> None:\n    t.status = 'x'\n",
        (
            "import app.models.tasks as m\nfrom sqlalchemy import update\n\n"
            "stmt = update(m.Task).values(status='x')\n"
        ),
    ]
    for source in sources:
        assert status_writes(source), source


def test_guard_detects_column_keyed_values() -> None:
    source = (
        "from sqlalchemy import update\nfrom app.models.tasks import Task\n\n"
        "stmt = update(Task).values({Task.status: 'x'})\n"
    )
    assert status_writes(source) == ["line 4: .values(status=...)"]


def test_guard_detects_split_raw_sql() -> None:
    source = (
        "COL = 'status'\n"
        "QUERY = f'UPDATE tasks SET {COL} = :s'\n"
        "OTHER = 'UPDATE tasks SET ' + 'status = :s'\n"
    )
    assert len(status_writes(source)) == 2


def test_guard_detects_constructor_and_committed_value_writes() -> None:
    source = (
        "from sqlalchemy.orm.attributes import set_committed_value\nfrom app.models.tasks import Task\n\n"
        "t = Task(status='completed')\nset_committed_value(t, 'status', 'x')\n"
    )
    assert len(status_writes(source)) == 2


def test_only_states_module_names_the_writer_marker() -> None:
    """The DB trigger trusts kk.status_writer; nothing but the state machine may set it."""
    offenders = [
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if path not in {ALLOWED_WRITER, APP / "models" / "tasks.py"}
        and ("kk.status_writer" in path.read_text() or "STATUS_WRITER" in path.read_text())
    ]
    assert offenders == []
