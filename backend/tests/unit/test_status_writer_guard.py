"""Only workflow/states.py may write tasks.status (CLAUDE.md #4, ARCHITECTURE §5.1).

A static scan of app/: a module that imports the Task model must not assign ``.status`` or pass ``status=`` to
``.values()``, and no module may contain raw SQL that updates tasks.status.

Second guard (#87, N60): outside ``TRANSITION_WRITERS`` no module may name ``transition`` or ``fail_task``
(import from any module, re-export, alias, ``*`` import of an ``app.*`` module or a relative ``*`` import,
name or attribute use, a ``match`` class pattern keyword, or a lookup with a constant or concatenated name
through ``getattr``, ``builtins.getattr``, an alias of it, ``__getattribute__`` or
``inspect.getattr_static``), and outside ``MARKER_HOLDERS`` no module may hold a string constant with the
whole word ``status_writer`` (the ``kk.status_writer`` marker, also when the whole word stays in one piece
across ``+``, f-string, ``%``, ``.format``, ``str.join``, SQL ``||`` or ``concat_ws``) or the
``STATUS_WRITER*`` constants. ``kk.approval_status_writer`` is another name. ``StepResult.transition`` is the
result type's API and is exempt only when ``StepResult`` is bound once, by a plain ``from app.workflow.engine
import StepResult``. The plain string ``"transition"`` (``kind``) is not flagged. The first guard also flags
``UPDATE [ONLY] [schema.]tasks [[AS] alias] SET ... status`` (case-insensitive, plain or quoted names) and
``table("tasks", ...)``.

Closed by the hardening owed before 3.2d (AG-13 3.2c2c1 delta; spec §16.6): the marker is matched without
regard to case (PostgreSQL reads a GUC name that way: ``'kk.Status_Writer'``, ``SET LOCAL kk.Status_Writer``),
and a marker split into pieces by ``||``, ``concat`` or ``U&`` is caught by ``test_guc_tripwire.py``, which
flags every way to set a GUC outside ``GUC_WRITERS``, whatever the name; raw SQL is read after comments,
whitespace, quotes and spaces around the dot are removed (``UPDATE/**/public . tasks AS "t" SET``) and also
covers ``tasks *``,
``MERGE ... UPDATE SET`` and ``INSERT ... ON CONFLICT DO UPDATE SET`` on ``status`` (or on a ``{}`` column);
a ``getattr`` alias bound by ``from builtins import getattr as g``, an annotated or walrus assignment, a tuple
pair or ``functools.partial``, ``<ref>.__call__``, a name held in a constant, and ``eval``/``exec`` of a
constant that names a writer. ``redis.eval`` is not a finding.

Still out of scope, accepted: ``operator.methodcaller`` and ``inspect.getmembers`` (the name is a run-time
value), ``ALTER ROLE|DATABASE ... SET``, ``ALTER SYSTEM SET``, a ``lambda`` wrapper around ``getattr``
(``g = lambda *a: getattr(*a)``), a driver connection setting under a key other than ``server_settings`` or
``options``, SQL held as ``bytes`` literals, and the indirect writers listed below. The import-linter
contract N60 sees imports only, never strings or SQL. A DB backstop is a backlog item.

Limits that a static scan cannot decide: ``getattr`` with a computed string, ``importlib``,
``operator.attrgetter``, ``vars()`` / ``__dict__`` (and ``globals()`` / ``sys.modules`` lookups),
a string built at run time (``%`` or ``.format`` of SQL, a name read from data); and the indirect writers,
which reach ``transition`` through an allowlisted module: ``project_tasks.cancel_open_tasks``, the
``tool_r3`` functions, ``recovery.recover_expired`` and ``TaskEngine._fail``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

# The string reader and SQL normaliser shared by the writer guards: one copy of the lexer.
from tests.static_scan import normalise_sql, paired_names, string_value

APP = Path(__file__).resolve().parents[2] / "app"
ALLOWED_WRITER = APP / "workflow" / "states.py"
TRANSITION_WRITERS = frozenset(
    {
        "workflow/states.py",
        "workflow/engine.py",
        "workflow/recovery.py",
        "services/project_tasks.py",
        "services/tool_r3.py",
    }
)
MARKER_HOLDERS = frozenset({"workflow/states.py", "models/tasks.py"})
WRITER_NAMES = frozenset({"transition", "fail_task"})
MARKER_NAMES = frozenset({"STATUS_WRITER_SETTING", "STATUS_WRITER"})
# Raw SQL is read after ``normalise_sql``: no comments, one space between words and none around ``.``.
_TASKS = r"(?:\w+\.)?tasks\b"
_UPDATE_TASKS = rf"\bupdate\s+(?:only\s+)?{_TASKS}(?:\s*\*)?(?:\s+(?:as\s+)?\w+)?\s+set\b"
_MERGE_UPDATE = rf"\bmerge\s+into\s+(?:only\s+)?{_TASKS}[^;]*?\bupdate\s+set\b"
_UPSERT_UPDATE = rf"\binsert\s+into\s+{_TASKS}[^;]*?\bon\s+conflict\b[^;]*?\bdo\s+update\s+set\b"
RAW_SQL = tuple(
    re.compile(prefix + r"[^;]*?\bstatus\b", re.IGNORECASE)
    for prefix in (_UPDATE_TASKS, _MERGE_UPDATE, _UPSERT_UPDATE)
)
DYNAMIC_COLUMN_SQL = tuple(
    re.compile(prefix + r"\s+\{\}", re.IGNORECASE)
    for prefix in (_UPDATE_TASKS, _MERGE_UPDATE, _UPSERT_UPDATE)
)
# PostgreSQL reads a GUC name without regard to case: ``kk.Status_Writer`` is the marker. The word boundary
# stays: three docstrings name ``test_status_writer_guard.py``.
MARKER_WORD = re.compile(r"\bstatus_writer\b", re.IGNORECASE)


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


def _is_raw_status_update(node: ast.expr) -> bool:
    if not isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp | ast.Call):
        return False
    value = string_value(node)
    if value is None:
        return False
    sql = normalise_sql(value)
    # A dynamic column ("{}") after UPDATE tasks SET could be status too.
    return any(p.search(sql) for p in (*RAW_SQL, *DYNAMIC_COLUMN_SQL))


def _is_tasks_table_call(node: ast.Call) -> bool:
    """``sqlalchemy.table("tasks", ...)``: a table ``update()`` can write without the model."""
    func = node.func
    named_table = (isinstance(func, ast.Name) and func.id == "table") or (
        isinstance(func, ast.Attribute) and func.attr == "table"
    )
    first = string_value(node.args[0]) if node.args else None
    return named_table and first is not None and first.strip().strip('"').lower() == "tasks"


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
    """Report a concatenation, f-string, ``%``, ``.format`` or ``join`` once, at its outermost node, not
    again for each part: some enclosing expression already reads as one string."""
    parent = parents.get(node)
    while isinstance(parent, ast.expr):
        if isinstance(parent, ast.JoinedStr | ast.BinOp | ast.Call) and string_value(parent) is not None:
            return True
        parent = parents.get(parent)
    return False


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
        if isinstance(node, ast.Call) and _is_tasks_table_call(node):
            found.append(f"line {node.lineno}: table('tasks') can be written without the model")
        if not uses_task:
            continue
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign | ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "status":
                found.append(f"line {getattr(node, 'lineno', 0)}: assigns .status")
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


def _bound_names(node: ast.AST) -> list[str]:
    """Names that ``node`` binds in its scope (imports, assignment targets, parameters, defs, handlers)."""
    if isinstance(node, ast.Import | ast.ImportFrom):
        return [(a.asname or a.name).split(".")[0] for a in node.names]
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        return [node.id]
    if isinstance(node, ast.arg):
        return [node.arg]
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return [node.name]
    if isinstance(node, ast.ExceptHandler) and node.name:
        return [node.name]
    if isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
        return [node.name]
    return []


def _step_result_is_plain(tree: ast.Module) -> bool:
    """``StepResult.transition`` is the result type's own API only when ``StepResult`` is bound exactly
    once in the module, by ``from app.workflow.engine import StepResult`` with no ``as``: any other binding
    (a second import, an assignment, a parameter) could make the name the states module."""
    binds = [n for node in ast.walk(tree) for n in _bound_names(node) if n == "StepResult"]
    plain = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.workflow.engine"
        and node.level == 0
        and any(a.name == "StepResult" and a.asname is None for a in node.names)
    ]
    return len(binds) == 1 and len(plain) == 1


_REFLECTIVE_ATTRS = frozenset({"getattr", "__getattribute__", "getattr_static"})
_WRITER_WORD = re.compile(r"\b(?:transition|fail_task)\b")


def _is_partial(node: ast.expr) -> bool:
    func = node.func if isinstance(node, ast.Call) else None
    return (isinstance(func, ast.Name) and func.id == "partial") or (
        isinstance(func, ast.Attribute) and func.attr == "partial"
    )


def _is_getattr_ref(node: ast.expr, aliases: set[str]) -> bool:
    """A reference to a reflective lookup: ``getattr``, ``builtins.getattr``, ``object.__getattribute__``,
    ``inspect.getattr_static``, an alias of one, a walrus that binds one, ``<ref>.__call__`` or
    ``functools.partial(<ref>, ...)``."""
    if isinstance(node, ast.Name):
        return node.id == "getattr" or node.id in aliases
    if isinstance(node, ast.Attribute):
        return node.attr in _REFLECTIVE_ATTRS or (
            node.attr == "__call__" and _is_getattr_ref(node.value, aliases)
        )
    if isinstance(node, ast.NamedExpr):
        return _is_getattr_ref(node.value, aliases)
    if isinstance(node, ast.Call) and _is_partial(node):
        return bool(node.args) and _is_getattr_ref(node.args[0], aliases)
    return False


def _bind(target: ast.expr, value: ast.expr, aliases: set[str]) -> None:
    """Add the names ``target = value`` binds to a reflective ref; a tuple target pairs with a tuple value."""
    if isinstance(target, ast.Tuple | ast.List) and isinstance(value, ast.Tuple | ast.List):
        if len(target.elts) == len(value.elts):
            for t, v in zip(target.elts, value.elts, strict=True):
                _bind(t, v, aliases)
    elif isinstance(target, ast.Name) and _is_getattr_ref(value, aliases):
        aliases.add(target.id)


def _getattr_aliases(tree: ast.Module) -> set[str]:
    """Names bound to a reflective lookup, by ``=``, annotated ``=``, ``:=``, a tuple pair or
    ``from builtins import getattr as g``; repeated until no new name appears (``h = g``)."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            aliases |= {a.asname for a in node.names if a.name == "getattr" and a.asname}
    while True:
        before = len(aliases)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    _bind(target, node.value, aliases)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                _bind(node.target, node.value, aliases)
            elif isinstance(node, ast.NamedExpr):
                _bind(node.target, node.value, aliases)
        if len(aliases) == before:
            return aliases


def _constant_names(tree: ast.Module) -> dict[str, set[str]]:
    """Every string a name is assigned (``NAME = 'transition'``, ``NAME: str = ...``, ``N, M = 'a', 'b'``):
    ``getattr(m, NAME)`` reads as its values."""
    values: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            pairs = [p for t in node.targets for p in paired_names(t, node.value)]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = list(paired_names(node.target, node.value))
        else:
            continue
        for name, value in pairs:
            if (text := string_value(value)) is not None:
                values.setdefault(name, set()).add(text)
    return values


def _names_a_writer(arg: ast.expr, constants: dict[str, set[str]]) -> bool:
    if isinstance(arg, ast.Name):
        return bool(constants.get(arg.id, set()) & WRITER_NAMES)
    return string_value(arg) in WRITER_NAMES


def _is_reflective_writer_lookup(call: ast.Call, aliases: set[str], constants: dict[str, set[str]]) -> bool:
    """A reflective lookup with a writer name; for ``partial(getattr, m, 'transition')`` the name sits in the
    partial call itself, for ``partial(getattr, m)('transition')`` in the outer call."""
    if _is_partial(call):
        return _is_getattr_ref(call, aliases) and any(_names_a_writer(a, constants) for a in call.args[1:3])
    return _is_getattr_ref(call.func, aliases) and any(_names_a_writer(a, constants) for a in call.args[:2])


def _is_eval_of_a_writer(call: ast.Call) -> bool:
    """The builtin ``eval``/``exec`` (a Name; ``redis.eval`` is an Attribute) on a constant that names one."""
    func = call.func
    if not (isinstance(func, ast.Name) and func.id in {"eval", "exec"} and call.args):
        return False
    text = string_value(call.args[0])
    return text is not None and bool(_WRITER_WORD.search(text))


def writer_refs(source: str) -> list[str]:
    """References to transition/fail_task (BC-18), by AST; plain strings such as "transition" are not."""
    tree = ast.parse(source)
    plain_step_result = _step_result_is_plain(tree)
    aliases = _getattr_aliases(tree)
    constants = _constant_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.ImportFrom):
            if any(a.name == "*" for a in node.names) and (
                node.level > 0 or (node.module or "").startswith("app.")
            ):
                found.append(f"line {line}: star import of {'.' * node.level}{node.module or ''}")
            found += [f"line {line}: imports {a.name}" for a in node.names if a.name in WRITER_NAMES]
        elif isinstance(node, ast.Name) and node.id in WRITER_NAMES:
            found.append(f"line {line}: names {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in WRITER_NAMES:
            is_api = (
                plain_step_result
                and node.attr == "transition"
                and isinstance(node.value, ast.Name)
                and node.value.id == "StepResult"
            )
            if not is_api:
                found.append(f"line {line}: attribute {node.attr}")
        elif isinstance(node, ast.Call) and (
            _is_reflective_writer_lookup(node, aliases, constants) or _is_eval_of_a_writer(node)
        ):
            found.append(f"line {line}: reflective lookup of a writer")
        elif isinstance(node, ast.MatchClass) and WRITER_NAMES & set(node.kwd_attrs):
            found.append(f"line {line}: match class pattern on a writer")
    return found


def marker_refs(source: str) -> list[str]:
    """The kk.status_writer marker, in any string form, or its constants (BC-18 #6)."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp | ast.Call):
            value = string_value(node)
            if (
                value is not None
                and MARKER_WORD.search(value)
                and not _inside_string_expression(node, parents)
            ):
                found.append(f"line {line}: marker text")
        elif isinstance(node, ast.Name) and node.id in MARKER_NAMES:
            found.append(f"line {line}: names {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in MARKER_NAMES:
            found.append(f"line {line}: attribute {node.attr}")
        elif isinstance(node, ast.ImportFrom):
            found += [f"line {line}: imports {a.name}" for a in node.names if a.name in MARKER_NAMES]
    return found


def _rel(path: Path) -> str:
    return path.relative_to(APP).as_posix()


def test_only_listed_modules_name_transition_or_fail_task() -> None:
    violations = {
        _rel(path): hits
        for path in APP.rglob("*.py")
        if _rel(path) not in TRANSITION_WRITERS and (hits := writer_refs(path.read_text()))
    }
    assert violations == {}


def test_transition_allowlist_is_exactly_the_modules_that_reference_it() -> None:
    """Anti-bitrot: scanning without the allowlist finds exactly TRANSITION_WRITERS."""
    hit = {_rel(path) for path in APP.rglob("*.py") if writer_refs(path.read_text())}
    assert hit == TRANSITION_WRITERS


def test_marker_is_held_only_by_its_holders() -> None:
    violations = {
        _rel(path): hits
        for path in APP.rglob("*.py")
        if _rel(path) not in MARKER_HOLDERS and (hits := marker_refs(path.read_text()))
    }
    assert violations == {}


def test_marker_allowlist_is_exactly_the_modules_that_reference_it() -> None:
    hit = {_rel(path) for path in APP.rglob("*.py") if marker_refs(path.read_text())}
    assert hit == MARKER_HOLDERS


WRITER_SAMPLES = [
    "from app.workflow.states import transition\n",
    "from app.workflow.states import transition as t\n",
    "from app.workflow.engine import fail_task\n",
    "from app.services.project_tasks import transition\n",
    "from app.workflow.states import *\n",
    "import app.workflow.states as s\n\ns.transition(1)\n",
    "from app.workflow import states\n\nstates.fail_task(1)\n",
    "import app.workflow.states\n\napp.workflow.states.transition(1)\n",
    "getattr(states, 'transition')\n",
    "getattr(states, 'trans' + 'ition')\n",
    "from app.workflow.engine import StepResult as SR\n\nSR.transition(1, 'r')\n",
    # AG-13 PoC (3.2c2c1): the exempt name rebound, getattr by other routes, match, relative star import.
    (
        "from app.workflow.engine import StepResult\nfrom app.workflow import states as StepResult\n\n"
        "StepResult.transition(1)\n"
    ),
    (
        "from app.workflow.engine import StepResult\nfrom app.workflow import states\n\n"
        "def run(StepResult=states):\n    return StepResult.fail_task\n"
    ),
    "StepResult = object()\nStepResult.transition(1)\n",
    "import builtins\nfrom app.workflow import states\nw = builtins.getattr(states, 'transition')\n",
    "from app.workflow import states\ng = getattr\nw = g(states, 'fail_task')\n",
    "from app.workflow import states\nw = states.__getattribute__('transition')\n",
    "from app.workflow import states\nw = object.__getattribute__(states, 'fail_task')\n",
    "import inspect\nfrom app.workflow import states\nw = inspect.getattr_static(states, 'transition')\n",
    "from app.workflow import states\nmatch states:\n    case object(transition=w):\n        pass\n",
    "from .states import *\n",
    "from ..workflow.states import *\n",
    # AG-13 PoC (3.2c2c1 delta): getattr by aliases the binding scan missed, and eval/exec of a constant.
    "from builtins import getattr as g\nfrom app.workflow import states\nw = g(states, 'transition')\n",
    "from app.workflow import states\ng: object = getattr\nw = g(states, 'transition')\n",
    "from app.workflow import states\nw = (g := getattr)(states, 'fail_task')\n",
    "from app.workflow import states\ng, h = getattr, 0\nw = g(states, 'transition')\n",
    (
        "import functools\nfrom app.workflow import states\n"
        "w = functools.partial(getattr, states)('fail_task')\n"
    ),
    (
        "from functools import partial\nfrom app.workflow import states\n"
        "w = partial(getattr, states, 'transition')()\n"
    ),
    "from app.workflow import states\np = functools.partial(getattr, states)\nw = p('transition')\n",
    "from app.workflow import states\nw = object.__getattribute__.__call__(states, 'transition')\n",
    "from app.workflow import states\nw = eval('states.transition')\n",
    "exec('from app.workflow.states import transition')\n",
    "from app.workflow import states\nNAME = 'transition'\nw = getattr(states, NAME)\n",
    "from app.workflow import states\nN: str = 'transition'\nw = getattr(states, N)\n",
    "from app.workflow import states\nN, M = 'transition', 'x'\nw = getattr(states, N)\n",
]
MARKER_SAMPLES = [
    "X = 'kk.' + 'status_writer'\n",
    "X = f'kk.status_writer{1}'\n",
    "from app.models.tasks import STATUS_WRITER_SETTING\n",
    "from app.models import tasks\n\ntasks.STATUS_WRITER\n",
    # AG-13 PoC: the marker built where the AST sees only pieces.
    "Q = \"SELECT set_config('kk.' || 'status_writer', 'transition', true)\"\n",
    "M = 'kk.%s' % 'status_writer'\n",
    "M = 'kk.{}'.format('status_writer')\n",
    "M = '.'.join(['kk', 'status_writer'])\n",
    "Q = \"SELECT set_config('kk.Status_Writer', 'transition', true)\"\n",
    "Q = \"SET LOCAL kk.Status_Writer = 'transition'\"\n",
    "M = 'KK.STATUS_WRITER'\n",
    # AG-13 delta: pieces the AST can still join (constant f-string field, % / .format / join in pieces).
    "M = f\"kk.{'status'}_writer\"\n",
    "M = '_'.join(['kk.status', 'writer'])\n",
    "M = 'kk.%s_writer' % 'status'\n",
    "M = 'kk.status{}'.format('_writer')\n",
    "Q = \"SELECT set_config(concat_ws('.', 'kk', 'status_writer'), 'transition', true)\"\n",
]
STATUS_SQL_SAMPLES = [
    "Q = 'UPDATE public.tasks SET status = :s WHERE id = :id'\n",
    "Q = 'UPDATE tasks t SET status = :s WHERE t.id = :id'\n",
    "Q = 'UPDATE tasks AS t SET status = :s WHERE t.id = :id'\n",
    "Q = 'UPDATE ONLY tasks SET status = :s WHERE id = :id'\n",
    'Q = \'UPDATE "tasks" SET "status" = :s WHERE id = :id\'\n',
    "Q = 'update  public.\"tasks\"  as t\\n set status = 1'\n",
    "from sqlalchemy import table, column, update\nt = table('tasks', column('status'))\n",
    "import sqlalchemy as sa\nt = sa.table(\"tasks\", sa.column('status'))\n",
    # AG-13 delta: spacing, comments and a quoted alias inside the statement; other statements that write.
    "Q = 'UPDATE public . tasks SET status = :s WHERE id = :id'\n",
    "Q = 'UPDATE/**/tasks SET status = :s WHERE id = :id'\n",
    "Q = 'UPDATE tasks/**/SET status = :s WHERE id = :id'\n",
    "Q = 'UPDATE -- why\\n tasks SET status = :s'\n",
    'Q = \'UPDATE tasks AS "t" SET status = :s WHERE "t".id = :id\'\n',
    "Q = 'UPDATE tasks * SET status = :s WHERE id = :id'\n",
    "Q = 'UPDATE ONLY tasks * AS t SET status = :s'\n",
    "Q = 'MERGE INTO tasks t USING (SELECT 1) s ON t.id = :id WHEN MATCHED THEN UPDATE SET status = :s'\n",
    "Q = 'MERGE INTO public.tasks AS t USING s ON t.id = s.id WHEN MATCHED THEN UPDATE SET status = 1'\n",
    "Q = 'INSERT INTO tasks (id) VALUES (:id) ON CONFLICT (id) DO UPDATE SET status = :s'\n",
    "Q = 'insert into tasks (id) values (1) on conflict do update set \"status\" = excluded.status'\n",
    "Q = 'UPDATE tasks SET {col} = :s'.format(col='status')\n",
    "Q = 'UPDATE tasks SET {} = :s'\n",
]
CLEAN_SAMPLES = [
    "from app.workflow.engine import StepResult\n\nStepResult.transition(TaskStatus.PLANNED, 'r')\n",
    "kind = 'x'\nok = kind == 'transition'\n",
    '"""Docs mention workflow.states.transition and fail_task."""\n',
    "STATUS_WRITER_MODE = 'transition'\n",
    "from app.workflow.engine import StepResult\n\ndef f():\n    return StepResult.transition(1, 'r')\n",
    "kind = 'transition'\n",
    "from .states import thing\n",
    "SETTING = 'kk.approval_status_writer'\n",
    "NAME = 'approval_status_writer'\n",
    "Q = 'SELECT id, status FROM tasks WHERE id = :id'\n",
    "from sqlalchemy import table\nt = table('projects')\n",
    "g = getattr\nw = g(obj, 'name')\n",
    "r = redis.eval(_RELEASE, 1, key)\n",
    "import functools\ncb = functools.partial(_log_if_unrecorded, x)\n",
    "from functools import partial\nget = partial(getattr, obj)\nv = get('name')\n",
    '"see tests/unit/test_status_writer_guard.py"\n',
    'NOTE = "see tests/unit/test_status_writer_guard.py for the marker"\n',
    "Q = 'UPDATE tasks SET event_seq = event_seq + 1 WHERE id = :id'\n",
    'Q = "INSERT INTO tasks (id) VALUES (1) ON CONFLICT (id) DO UPDATE SET event_seq = excluded.event_seq"\n',
    "v = eval('1 + 1')\n",
]


def test_writer_guard_flags_every_known_idiom() -> None:
    for source in WRITER_SAMPLES:
        assert writer_refs(source), source


def test_marker_guard_flags_every_known_idiom() -> None:
    for source in MARKER_SAMPLES:
        assert marker_refs(source), source


def test_status_guard_flags_every_known_idiom() -> None:
    for source in STATUS_SQL_SAMPLES:
        assert status_writes(source), source


def test_guards_do_not_flag_reads_and_docs() -> None:
    for source in CLEAN_SAMPLES:
        assert writer_refs(source) == [], source
        assert marker_refs(source) == [], source
        assert status_writes(source) == [], source


def test_docstring_names_the_static_limits() -> None:
    doc = __doc__ or ""
    limits = (
        "getattr",
        "importlib",
        "operator.attrgetter",
        "vars()",
        "__dict__",
        "globals()",
        "sys.modules",
        "a string built at run time",
        "operator.methodcaller",
        "inspect.getmembers",
        "ALTER ROLE",
        "ALTER SYSTEM SET",
        "lambda",
        "bytes",
        "connection setting",
        "cancel_open_tasks",
        "tool_r3",
        "recover_expired",
        "TaskEngine._fail",
    )
    missing = [limit for limit in limits if limit not in doc]
    assert missing == []
