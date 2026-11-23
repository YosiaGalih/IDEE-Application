"""Only the listed modules set a GUC (CLAUDE.md #4/#5; AG-13 3.2c2c1 delta, spec §16.6).

``kk.status_writer`` and ``kk.approval_status_writer`` are session settings the DB triggers trust, and
PostgreSQL reads a setting name without regard to case. Matching the marker text (the guards of
``tasks.status`` and ``approvals.status``) misses a name written in pieces
(``'kk.status' || '_writer'``, ``concat``, ``U&'...'``).
This tripwire does not look for the marker: it flags the *ways to set a GUC* in ``app/``: a ``set_config``
call, name or string (any case), ``SET [LOCAL|SESSION] <name> =`` at the start of a string or after ``;``,
``server_settings`` and ``options -c name=``. Outside ``GUC_WRITERS`` each is a violation; inside it the
name must be a constant that the module's entry lists (a literal, a module constant, or a
``KNOWN_GUC_CONSTANTS`` name).
A name the scan cannot read (``||``, ``concat``, a variable, ``U&``) is always a violation.

Not decided by a static scan, accepted: a name computed at run time (``getattr`` / ``eval`` of data, a string
read from a file or the DB), ``operator.methodcaller``, ``inspect.getmembers``,
``ALTER ROLE|DATABASE ... SET``, ``sys.modules`` / ``globals()`` lookups, and an indirect writer through a
module of ``GUC_WRITERS``
(``workflow.states.transition`` sets the marker for any caller). The DB backstop is a backlog item.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.models import approvals as approvals_model
from app.models import tasks as tasks_model
from tests.unit.test_agent_run_writer_guard import _normalise_sql, _string_value
from tests.unit.test_status_writer_guard import marker_refs

APP = Path(__file__).resolve().parents[2] / "app"
KNOWN_GUC_CONSTANTS = {
    "STATUS_WRITER_SETTING": "kk.status_writer",
    "APPROVAL_WRITER_SETTING": "kk.approval_status_writer",
}
GUC_WRITERS: dict[str, set[str]] = {
    "core/db.py": {"lock_timeout", "statement_timeout", "idle_in_transaction_session_timeout"},
    "workflow/lease.py": {"lock_timeout"},
    "services/projects.py": {"lock_timeout"},
    "workflow/states.py": {"kk.status_writer"},
    "services/approval_store.py": {"kk.approval_status_writer"},
}
UNREADABLE = "<unreadable>"


_SET_CONFIG = re.compile(r"\bset_config\b", re.IGNORECASE)
_SET_CONFIG_CALL = re.compile(r"\bset_config\s*\(\s*([\w.]+)\s*,", re.IGNORECASE)
# ``SET [LOCAL|SESSION] name =|TO`` at the start of the SQL or after ``;`` (read after ``_normalise_sql``).
_SET_STATEMENT = re.compile(
    r"(?:^|;)\s*set\s+(?:(?:local|session)\s+)?([\w.]+)\s*(?:=|\bto\b)", re.IGNORECASE
)
_OPTIONS_C = re.compile(r"(?:^|\s)-c\s*([\w.]+)\s*=", re.IGNORECASE)
_Use = tuple[int, str, str | None]


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """``NAME = 'literal'`` assigned in the module: a name argument that is such a constant reads as it."""
    values: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and (text := _string_value(node.value)) is not None
            and "{}" not in text
        ):
            values |= {t.id: text for t in node.targets if isinstance(t, ast.Name)}
    return values


def _guc_name(arg: ast.expr, constants: dict[str, str]) -> str | None:
    """The setting name a ``set_config`` call passes: a string literal, a module constant, or a
    ``KNOWN_GUC_CONSTANTS`` name (imported); lower case. None when the scan cannot read it."""
    text = _string_value(arg)
    if text is not None:
        return None if "{}" in text else text.lower()
    if isinstance(arg, ast.Name):
        if arg.id in constants:
            return constants[arg.id].lower()
        if arg.id in KNOWN_GUC_CONSTANTS:
            return KNOWN_GUC_CONSTANTS[arg.id].lower()
    if isinstance(arg, ast.Attribute) and arg.attr in KNOWN_GUC_CONSTANTS:
        return KNOWN_GUC_CONSTANTS[arg.attr].lower()
    return None


def _string_uses(value: str, line: int) -> list[_Use]:
    found: list[_Use] = []
    sql = _normalise_sql(value)
    for hit in _SET_CONFIG.finditer(sql):
        call = _SET_CONFIG_CALL.match(sql, hit.start())
        found.append((line, "set_config", call.group(1).lower() if call else None))
    found += [(line, "SET", m.group(1).lower()) for m in _SET_STATEMENT.finditer(sql)]
    found += [(line, "options -c", m.group(1).lower()) for m in _OPTIONS_C.finditer(value)]
    if "server_settings" in value.lower():
        found.append((line, "server_settings", None))
    return found


def guc_uses(source: str) -> list[_Use]:
    """(line, kind, lower-case name or None when the scan cannot read it) of each way to set a GUC."""
    tree = ast.parse(source)
    constants = _module_constants(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    found: set[_Use] = set()
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        parent = parents.get(node)
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp | ast.Call):
            docstring = isinstance(parent, ast.Expr) and isinstance(node, ast.Constant)
            value = None if docstring else _string_value(node)
            if value is not None:
                found.update(_string_uses(value, line))
        if isinstance(node, ast.Call):
            func = node.func
            called = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if called.lower() == "set_config":
                found.add((line, "set_config", _guc_name(node.args[0], constants) if node.args else None))
            found.update((line, "server_settings", None) for k in node.keywords if k.arg == "server_settings")
        elif isinstance(node, ast.Name | ast.Attribute):
            ident = node.id if isinstance(node, ast.Name) else node.attr
            is_callee = isinstance(parent, ast.Call) and parent.func is node
            if ident.lower() == "set_config" and not is_callee:
                found.add((line, "set_config", None))
            if ident.lower() == "server_settings":
                found.add((line, "server_settings", None))
        elif isinstance(node, ast.ImportFrom):
            found.update((line, "set_config", None) for a in node.names if a.name.lower() == "set_config")
            found.update(
                (line, "server_settings", None) for a in node.names if a.name.lower() == "server_settings"
            )
    return sorted(found, key=lambda use: (use[0], use[1], use[2] or ""))


def _rel(path: Path) -> str:
    return path.relative_to(APP).as_posix()


def _scan_app() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in APP.rglob("*.py"):
        if uses := guc_uses(path.read_text()):
            found[_rel(path)] = {name or UNREADABLE for _, _, name in uses}
    return found


def violations(rel: str, source: str) -> list[str]:
    return [
        f"line {line}: {kind} {name or UNREADABLE}"
        for line, kind, name in guc_uses(source)
        if kind == "server_settings" or name is None or name not in GUC_WRITERS.get(rel, set())
    ]


MARKER_POC = {
    "SQL || split inside the word": (
        "Q = \"SELECT set_config('kk.status' || '_writer', 'transition', true)\"\n"
    ),
    "SQL concat() split inside the word": (
        "Q = \"SELECT set_config(concat('kk.status', '_writer'), 'transition', true)\"\n"
    ),
    "GUC name in another case": "Q = \"SELECT set_config('kk.Status_Writer', 'transition', true)\"\n",
    "SET LOCAL, other case": "Q = \"SET LOCAL kk.Status_Writer = 'transition'\"\n",
    "f-string with a constant field": "M = f\"kk.{'status'}_writer\"\n",
    "'_'.join(['kk.status', 'writer'])": "M = '_'.join(['kk.status', 'writer'])\n",
    "'kk.%s_writer' % 'status'": "M = 'kk.%s_writer' % 'status'\n",
    "'kk.status{}'.format('_writer')": "M = 'kk.status{}'.format('_writer')\n",
    "SQL U& escape": "Q = \"SELECT set_config(U&'kk.status\\\\005fwriter', 'transition', true)\"\n",
}
TRIPWIRE_SAMPLES = [
    "from sqlalchemy import func\nq = func.set_config('kk.status_writer', 'transition', True)\n",
    "from sqlalchemy import func\nq = func.SET_CONFIG('anything', 'x', True)\n",
    "q = \"SELECT set_config('app.other', 'x', true)\"\n",
    "q = \"SELECT SET_CONFIG('app.other', 'x', true)\"\n",
    "q = \"SELECT set_config('kk.' || 'status_writer', 'transition', true)\"\n",
    "q = \"SELECT set_config(concat('kk.', 'status_writer'), 'transition', true)\"\n",
    "q = \"SELECT set_config(U&'kk.status\\\\005fwriter', 'transition', true)\"\n",
    "q = f\"SELECT set_config('{name}', 'x', true)\"\n",
    "q = \"SELECT set_con\" + \"fig('lock_timeout', '1s', true)\"\n",
    "from sqlalchemy import func\nf = getattr(func, 'set_config')\n",
    "from sqlalchemy import func\nf = func.set_config\nq = f('app.x', 'y', True)\n",
    "from sqlalchemy.sql.functions import set_config as sc\n",
    "from sqlalchemy import func\nq = func.set_config(name, 'x', True)\n",
    'q = "SET LOCAL app.x = 1"\n',
    'q = "set session app.x to 1"\n',
    "q = \"SELECT 1; SET LOCAL kk.status_writer = 'transition'\"\n",
    'q = "SELECT 1;  /* c */ SET app.x = 1"\n',
    'q = "-- c\\nSET LOCAL app.x = 1"\n',
    (
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "e = create_async_engine(u, connect_args={'server_settings': {'kk.status_writer': 'transition'}})\n"
    ),
    "e = make(server_settings={'a': 'b'})\n",
    "SS = 'server_settings'\n",
    (
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "e = create_async_engine(u, connect_args={'options': '-c kk.status_writer=transition'})\n"
    ),
]
TRIPWIRE_CLEAN = [
    "x = 'set the value'\n",
    '"""Docs mention set_config( and SET LOCAL x = 1 and server_settings."""\n',
    "q = 'SELECT id FROM tasks WHERE name = :n'\n",
    "q = 'UPDATE tasks SET event_seq = event_seq + 1'\n",
    "q = 'INSERT INTO t (a) VALUES (1) ON CONFLICT DO UPDATE SET a = 2'\n",
    "r = redis.set('k', 'v')\n",
]


def test_tripwire_flags_every_known_way_to_set_a_guc() -> None:
    for source in TRIPWIRE_SAMPLES:
        assert violations("services/other.py", source), source


def test_tripwire_ignores_prose_and_other_statements() -> None:
    for source in TRIPWIRE_CLEAN:
        assert guc_uses(source) == [], source


def test_every_marker_spelling_of_the_ag13_delta_is_caught() -> None:
    for label, source in MARKER_POC.items():
        assert marker_refs(source) or guc_uses(source), label


def test_an_unreadable_name_is_flagged_even_in_an_allowlisted_module() -> None:
    for source in (MARKER_POC["SQL || split inside the word"], MARKER_POC["SQL U& escape"]):
        assert violations("workflow/states.py", source), source
    assert violations("workflow/states.py", "q = func.set_config('kk.Status_Writer', 'x', True)\n") == []
    assert violations("workflow/states.py", "q = func.set_config('lock_timeout', 'x', True)\n")


def test_known_constants_read_as_their_model_value() -> None:
    source = "q = func.set_config(STATUS_WRITER_SETTING, STATUS_WRITER, True)\n"
    assert violations("workflow/states.py", source) == []
    assert violations("workflow/lease.py", source)
    assert (
        violations("services/approval_store.py", "q = func.set_config(APPROVAL_WRITER_SETTING, '', True)\n")
        == []
    )
    assert (
        violations("workflow/states.py", "q = func.set_config(tasks.STATUS_WRITER_SETTING, '', True)\n") == []
    )


def test_known_guc_constants_equal_the_model_values() -> None:
    assert KNOWN_GUC_CONSTANTS == {
        "STATUS_WRITER_SETTING": tasks_model.STATUS_WRITER_SETTING,
        "APPROVAL_WRITER_SETTING": approvals_model.APPROVAL_WRITER_SETTING,
    }


def test_only_the_listed_modules_set_a_guc() -> None:
    bad = {
        _rel(path): hits for path in APP.rglob("*.py") if (hits := violations(_rel(path), path.read_text()))
    }
    assert bad == {}


def test_guc_writers_is_exactly_the_modules_and_names_found_without_the_allowlist() -> None:
    """Anti-bitrot: scanning without the allowlist finds exactly GUC_WRITERS (a stale name fails too)."""
    assert _scan_app() == GUC_WRITERS
