"""Only the listed modules set a GUC (CLAUDE.md #4/#5; AG-13 3.2c2c1 delta, spec §16.6).

``kk.status_writer`` and ``kk.approval_status_writer`` are session settings the DB triggers trust, and
PostgreSQL reads a setting name without regard to case. Matching the marker text (the guards of
``tasks.status`` and ``approvals.status``) misses a name written in pieces
(``'kk.status' || '_writer'``, ``concat``, ``U&'...'``).
This tripwire does not look for the marker: it flags the *ways to set a GUC* in ``app/``: a ``set_config``
call, name or string (any case), ``SET [LOCAL|SESSION] <name> =`` at the start of a string or after ``;``,
``server_settings`` and libpq ``options``: ``-c name=`` anywhere, ``--name=`` anywhere for a dotted name, and
any ``--name=`` in the value of an ``options=`` argument or ``'options'`` key (a value there that is not a
dict or list and that the scan cannot read is a violation too). Outside
``GUC_WRITERS`` each is a violation; inside it the name must be a constant that the module's entry lists (a
literal, a module constant, or a ``KNOWN_GUC_CONSTANTS`` name).
A name the scan cannot read (``||``, ``concat``, a variable, ``U&``, a computed piece of ``SET LOCAL`` by
f-string, ``+``, ``%``, ``.format`` or ``join``) is always a violation when it is the name of a way listed
above; a ``||`` inside a SQL string is read only through the forms below (before fix wave B, ``||`` inside a
``DO`` body passed).

Closed by fix wave B (AG-13 3.2c2c2 LOW-1): a SQL string is also a violation, in every module and with no
name that makes it allowed, when it holds a ``DO $...$`` block (any tag), ``UESCAPE``, an SQL ``EXECUTE`` of a
string or ``format(``, ``CREATE [OR REPLACE] FUNCTION|PROCEDURE``, ``ALTER FUNCTION|PROCEDURE|TABLE``,
``DISABLE TRIGGER`` or ``DROP TRIGGER``, ``pg_settings``, or ``SET LOCAL|SESSION <x>`` at any position (not
only at the start or after ``;``). ``||`` inside a ``DO`` body, a ``UESCAPE`` function name
(``U&"set!005fconfig" UESCAPE '!'(...)``) and a ``SET`` clause of ``CREATE FUNCTION`` are those forms. A plain
``UPDATE t SET col =`` is ordinary SQL and is not flagged. ``app/`` holds none of these as SQL strings (only
Python ``.execute(`` calls; the privilege name ``'EXECUTE'`` in ``core/audit.py`` is not the statement).

Not decided by a static scan, accepted: a name computed at run time (``getattr`` / ``eval`` of data, a string
read from a file or the DB), ``operator.methodcaller``, ``inspect.getmembers``,
``ALTER ROLE|DATABASE ... SET``, ``ALTER SYSTEM SET``, a ``lambda`` wrapper around ``getattr``
(``g = lambda *a: getattr(*a)``), a driver connection setting under a key other than ``server_settings`` or
``options``, SQL held as ``bytes`` literals, ``sys.modules`` / ``globals()`` lookups, and an indirect writer
through a module of ``GUC_WRITERS``
(``workflow.states.transition`` sets the marker for any caller). The DB backstop is a backlog item.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.models import approvals as approvals_model
from app.models import tasks as tasks_model
from tests.static_scan import concat_text, normalise_sql, paired_names, string_value, strip_comments
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
# ``SET [LOCAL|SESSION] name =|TO`` at the start of the SQL or after ``;`` (read after ``normalise_sql``).
_SET_STATEMENT = re.compile(
    r"(?:^|;)\s*set\s+(?:(?:local|session)\s+)?([\w.{}]+)\s*(?:=|\bto\b)", re.IGNORECASE
)
# libpq ``options`` takes ``-c name=value`` and ``--name=value``. The ``--`` form needs a dotted name (the
# marker GUCs are ``kk.*``): app/ has flags of ``git`` and ``prlimit`` (``--format=``, ``--as=``).
# SQL that sets or hides a GUC in a form no name can make readable (fix wave B, AG-13 LOW-1): a ``DO`` block
# (its body ``EXECUTE``s text built at run time), ``UESCAPE`` (``U&"set!005fconfig" UESCAPE '!'(...)`` is
# ``set_config``), SQL ``EXECUTE`` of a string, function and procedure DDL (a ``SET`` clause or a body of its
# own), ``ALTER TABLE`` (``DISABLE TRIGGER``), ``DROP|DISABLE TRIGGER``, ``pg_settings`` (``UPDATE`` of it
# is a ``SET``) and ``SET LOCAL|SESSION`` at any position. Read on the text with comments removed; the
# ``EXECUTE`` form on the text before quotes are removed. A plain ``UPDATE t SET col =`` is ordinary SQL.
_FORBIDDEN_NORMALISED = {
    "DO block": re.compile(r"\bdo\s+\$\w*\$", re.IGNORECASE),
    "UESCAPE": re.compile(r"\buescape\b", re.IGNORECASE),
    "function or procedure DDL": re.compile(
        r"\b(?:create\s+(?:or\s+replace\s+)?(?:function|procedure)|alter\s+(?:function|procedure))\b",
        re.IGNORECASE,
    ),
    "ALTER TABLE": re.compile(r"\balter\s+table\b", re.IGNORECASE),
    "DISABLE|DROP TRIGGER": re.compile(r"\b(?:disable|drop)\s+trigger\b", re.IGNORECASE),
    "pg_settings": re.compile(r"\bpg_settings\b", re.IGNORECASE),
    "SET LOCAL|SESSION": re.compile(r"\bset\s+(?:local|session)\s+[\w.{}\"]", re.IGNORECASE),
}
_FORBIDDEN_EXECUTE = re.compile(r"(?<![\'\"])\bexecute\s*(?:format\s*\(|[eEuU]?&?['\"$])", re.IGNORECASE)
_OPTIONS_C = re.compile(r"(?:^|\s)(?:-c\s*([\w.]+)|--(\w+\.[\w.]+))\s*=", re.IGNORECASE)
# The value of an ``options`` argument or ``'options'`` key is libpq's: there ``--name=`` sets any GUC, dotted
# or not (``--lock_timeout=1``), and a ``{}`` in the name is a piece the scan cannot read.
_OPTIONS_ARG = re.compile(r"(?:^|\s)(?:-c\s*|--)([\w.{}]+)\s*=", re.IGNORECASE)
_Use = tuple[int, str, str | None]


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """``NAME = 'literal'`` (also ``NAME: str = ...`` and ``N, M = 'a', 'b'``) assigned in the module: a name
    argument that is such a constant reads as it."""
    values: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            pairs = [p for t in node.targets for p in paired_names(t, node.value)]
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs = list(paired_names(node.target, node.value))
        else:
            continue
        for name, value in pairs:
            if (text := string_value(value)) is not None and "{}" not in text:
                values[name] = text
    return values


def _guc_name(arg: ast.expr, constants: dict[str, str]) -> str | None:
    """The setting name a ``set_config`` call passes: a string literal, a module constant, or a
    ``KNOWN_GUC_CONSTANTS`` name (imported); lower case. None when the scan cannot read it."""
    text = string_value(arg)
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


def _option_values(node: ast.expr) -> list[ast.expr]:
    """The values passed as libpq ``options``: an ``options=`` keyword, or an ``'options'`` dict key."""
    if isinstance(node, ast.Call):
        return [k.value for k in node.keywords if k.arg == "options"]
    if isinstance(node, ast.Dict):
        return [
            v
            for k, v in zip(node.keys, node.values, strict=True)
            if k is not None and string_value(k) == "options"
        ]
    return []


def _option_uses(value: ast.expr, line: int) -> list[_Use]:
    """A dict or list value is another library's options (Celery ``{'expires': ...}``); a string is read with
    ``_OPTIONS_ARG``; anything else (a variable, a call) is an options string the scan cannot read."""
    if isinstance(value, ast.Dict | ast.List | ast.Tuple | ast.Set) or (
        isinstance(value, ast.Constant) and not isinstance(value.value, str)
    ):
        return []
    text = string_value(value)
    if text is None:
        text = concat_text(value)
    if text is None:
        return [(line, "options", None)]
    return [
        (line, "options", None if "{}" in m.group(1) else m.group(1).lower())
        for m in _OPTIONS_ARG.finditer(text)
    ]


def _string_uses(value: str, line: int) -> list[_Use]:
    found: list[_Use] = []
    sql = normalise_sql(value)
    for hit in _SET_CONFIG.finditer(sql):
        call = _SET_CONFIG_CALL.match(sql, hit.start())
        found.append((line, "set_config", call.group(1).lower() if call else None))
    # A ``{}`` in the token is a piece the scan cannot read (``f'SET LOCAL {n} = 1'``): an unreadable name.
    found += [
        (line, "SET", None if "{}" in m.group(1) else m.group(1).lower())
        for m in _SET_STATEMENT.finditer(sql)
    ]
    found += [
        (line, f"forbidden SQL ({label})", None)
        for label, p in _FORBIDDEN_NORMALISED.items()
        if p.search(sql)
    ]
    if _FORBIDDEN_EXECUTE.search(strip_comments(value)):
        found.append((line, "forbidden SQL (EXECUTE)", None))
    found += [(line, "options -c", (m.group(1) or m.group(2)).lower()) for m in _OPTIONS_C.finditer(value)]
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
            value = None if docstring else string_value(node)
            if value is None and not docstring:
                value = concat_text(node)
            if value is not None:
                found.update(_string_uses(value, line))
        for option in _option_values(node) if isinstance(node, ast.Call | ast.Dict) else []:
            found.update(_option_uses(option, getattr(option, "lineno", line)))
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
    "q = f'SET LOCAL {n} = 1'\n",
    "q = f'SET LOCAL kk.{n} = 1'\n",
    "q = 'SET LOCAL ' + n + ' = 1'\n",
    "q = ' '.join(['SET LOCAL', n, '= 1'])\n",
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
    "e = create_async_engine(u, connect_args={'options': '--kk.status_writer=x'})\n",
    "e = create_async_engine(u, connect_args={'options': '--lock_timeout=1'})\n",
    "c = connect(dsn, options='--session_replication_role=replica')\n",
    "c = connect(dsn, options=f'--{name}=1')\n",
    "c = connect(dsn, options=opts)\n",
    "e = create_async_engine(u, connect_args={'options': opts})\n",
    "O = '--kk.other=1'\n",
]
# Fix wave B (AG-13 LOW-1): SQL that sets or hides a GUC without ``set_config(`` at the start of a statement.
# None of these is ever allowed, in any module (``GUC_WRITERS`` too).
FORBIDDEN_SQL_SAMPLES = {
    "N1 DO block, EXECUTE of a split SET": (
        "Q = \"DO $$ BEGIN EXECUTE 'SET LOCAL kk.status' || '_writer = ''transition'''; END $$\"\n"
    ),
    "N1 DO block, tagged dollar quote": (
        "Q = \"DO $kk$ BEGIN EXECUTE 'SET LOCAL kk.status' || '_writer = 1'; END $kk$\"\n"
    ),
    "N1 DO block, comment before the quote": "Q = 'DO /* x */ $$ BEGIN NULL; END $$'\n",
    "A1 DO block, approval marker": (
        "Q = \"DO $$ BEGIN EXECUTE 'SET LOCAL kk.approval_status' || '_writer = ''approval_store'''; "
        'END $$"\n'
    ),
    "N3 U&identifier UESCAPE": (
        "Q = \"SELECT U&\\\"set!005fconfig\\\" UESCAPE '!'('kk.status' || '_writer', 'transition', true)\"\n"
    ),
    "A3 U&identifier UESCAPE, approval": (
        "Q = \"SELECT U&\\\"set!005fconfig\\\" UESCAPE '!'('kk.approval' || '_status_writer', 'x', true)\"\n"
    ),
    "N4 CREATE FUNCTION ... SET U&name": (
        'Q = "CREATE FUNCTION f() RETURNS void LANGUAGE sql '
        "SET U&\\\"kk.status\\\\005fwriter\\\" = 'transition' AS 'SELECT 1'\"\n"
    ),
    "S5 UESCAPE on a column": "Q = \"UPDATE tasks SET U&\\\"st!0061tus\\\" UESCAPE '!' = 'failed'\"\n",
    "EXECUTE of a string": "Q = \"EXECUTE 'SET LOCAL x = 1'\"\n",
    "EXECUTE format(": "Q = \"EXECUTE format('SET LOCAL %I = 1', n)\"\n",
    "EXECUTE, comment before the quote": "Q = \"EXECUTE/**/'SELECT 1'\"\n",
    "EXECUTE of a dollar quote": "Q = 'EXECUTE $$SELECT 1$$'\n",
    "CREATE FUNCTION": "Q = 'CREATE FUNCTION f() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql'\n",
    "CREATE OR REPLACE FUNCTION": "Q = 'create or replace function f() returns int as $$ select 1 $$'\n",
    "CREATE PROCEDURE": "Q = 'CREATE PROCEDURE p() LANGUAGE sql AS $$ SELECT 1 $$'\n",
    "CREATE OR REPLACE PROCEDURE": "Q = 'CREATE OR REPLACE PROCEDURE p() AS $$ SELECT 1 $$'\n",
    "ALTER TABLE": "Q = 'ALTER TABLE tasks ADD COLUMN x int'\n",
    "ALTER FUNCTION ... SET": "Q = 'ALTER FUNCTION f() SET kk.x = 1'\n",
    "ALTER PROCEDURE": "Q = 'ALTER PROCEDURE p() RENAME TO q'\n",
    "DISABLE TRIGGER": "Q = 'ALTER TABLE tasks DISABLE TRIGGER USER'\n",
    "DISABLE TRIGGER alone": "Q = 'disable trigger tasks_status_guard'\n",
    "DROP TRIGGER": "Q = 'DROP TRIGGER tasks_status_guard ON tasks'\n",
    "SET LOCAL after a keyword": "Q = 'SELECT 1 FROM t SET LOCAL app.x = 1'\n",
    "SET SESSION mid-string": "Q = 'x SET SESSION app.x TO 1'\n",
    "SET LOCAL in a fragment": "Q = 'BEGIN; SELECT 1 ' + ' SET LOCAL a.b = 1'\n",
    "UPDATE pg_settings": "Q = \"UPDATE pg_settings SET setting = 'x' WHERE name = 'kk.' || 'x'\"\n",
}
TRIPWIRE_CLEAN = [
    "x = 'set the value'\n",
    '"""Docs mention set_config( and SET LOCAL x = 1 and server_settings."""\n',
    "q = 'SELECT id FROM tasks WHERE name = :n'\n",
    "q = 'UPDATE tasks SET event_seq = event_seq + 1'\n",
    "q = 'INSERT INTO t (a) VALUES (1) ON CONFLICT DO UPDATE SET a = 2'\n",
    "r = redis.set('k', 'v')\n",
    "argv = ['git', 'log', '--format=%H', '--as=x']\n",
    "beat = {'sweep': {'task': 't', 'options': {'expires': 5}}}\n",
    "q = select(Task).options(selectinload(Task.events))\n",
    "s = ', '.join([a, b])\n",
    "q = 'UPDATE tasks SET status = :s WHERE id = :i'\n",
    "q = 'UPDATE projects SET name = :n, updated_at = now()'\n",
    "q = 'INSERT INTO t (a) VALUES (1) ON CONFLICT (a) DO UPDATE SET a = excluded.a'\n",
    "q = 'SELECT * FROM t WHERE x = :x FOR UPDATE'\n",
    "r = await session.execute(stmt)\n",
    "q = \"SELECT has_function_privilege(current_user, p.oid, 'EXECUTE')\"\n",
    "m = 'cannot execute the step; do not drop the table'\n",
    "m = 'the table was altered; create a function later'\n",
    "m = 'reset the session; set the local value'\n",
]


def test_tripwire_flags_every_known_way_to_set_a_guc() -> None:
    for source in TRIPWIRE_SAMPLES:
        assert violations("services/other.py", source), source


def test_tripwire_flags_sql_that_sets_a_guc_without_set_config() -> None:
    for label, source in FORBIDDEN_SQL_SAMPLES.items():
        assert violations("services/other.py", source), label
        # never allowed, not even where a GUC is set: no name makes these readable
        for module in ("workflow/states.py", "services/approval_store.py", "core/db.py"):
            assert violations(module, source), (module, label)


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


def test_an_annotated_or_paired_constant_name_reads_as_its_value() -> None:
    for source in (
        "NAME: str = 'lock_timeout'\nq = func.set_config(NAME, '1s', True)\n",
        "NAME, OTHER = 'lock_timeout', 'x'\nq = func.set_config(NAME, '1s', True)\n",
    ):
        assert violations("workflow/lease.py", source) == [], source
        assert violations("services/other.py", source), source
        assert violations("workflow/states.py", source), source


def test_set_with_a_computed_name_is_unreadable_in_every_module() -> None:
    for source in (
        "q = f'SET LOCAL {n} = 1'\n",
        "q = f'SET LOCAL kk.{n} = 1'\n",
        "q = 'SET LOCAL ' + n + ' = 1'\n",
        "q = ' '.join(['SET LOCAL', n, '= 1'])\n",
        "c = connect(dsn, options=f'--{name}=1')\n",
    ):
        assert violations("workflow/states.py", source), source
        assert violations("core/db.py", source), source


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
