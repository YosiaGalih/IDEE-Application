"""Two modules write ``agent_runs``, one statement each (plan WP-3.1f2a Task 7, Keputusan plan #19; spec
WP-3.2 §16.3 Keputusan Lead #67, §16.3a #6):

- ``app/services/agent_runtime.py`` creates the row: ``AgentRunWriter.start``'s ``pg_insert(AgentRun)``, and
  no UPDATE;
- ``app/services/agent_run_rows.py`` closes it: ``close_run_row``'s ``update(AgentRun)``, and nothing else.

``AgentRunWriter.finish``/``fail`` and the run recovery (``app/services/run_recovery.py``, which now has none)
close a row only through ``close_run_row`` (tests/unit/test_close_run_row_guard.py). The pattern of
tests/unit/test_usage_writer_guard.py, hardened against the fourteen idioms of AG-13's PoC of 3.2c2a
(MEDIUM-1; Lead ruling R-F6: this guard, not an ORM mapper event, since test fixtures insert ``AgentRun`` rows
through the ORM). Outside the two writers no module:

- builds ``AgentRun(...)``, or calls ``insert``/``update``/``delete`` (under any name it imports them as from
  ``sqlalchemy``), ``pg_insert``, or the ``.insert()``/``.update()``/``.delete()`` method on the model, its
  ``__table__`` or ``getattr(module, "Agent" + "Run")``;
- names the table in ``table(...)``/``Table(...)``, or the model in ``query(...)`` or a ``bulk_*`` call;
- assigns to an attribute of, or ``setattr`` on, a value bound from an expression that names the model or
  such a value (``get(AgentRun, ...)``, ``select(AgentRun)``, ``scalars``, a loop over them); and, in a module
  that imports the model, assigns an attribute named like one of its columns, whatever the object;
- holds raw SQL that writes ``agent_runs``, schema-qualified or quoted, built by concatenation, f-string,
  ``.format`` or ``%``, the model's ``__tablename__`` reading as ``agent_runs``.

It reads what a module spells, not what runs: a loaded row handed to a helper in a module that never imports
the model, or a string built at run time, is beyond it.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Set
from pathlib import Path

import sqlalchemy as sa

from app.models.agents import AgentRun

APP = Path(__file__).resolve().parents[2] / "app"
ROW_CREATOR = APP / "services" / "agent_runtime.py"
ROW_CLOSER = APP / "services" / "agent_run_rows.py"
RECOVERY = APP / "services" / "run_recovery.py"
ALLOWED_WRITERS = {ROW_CREATOR, ROW_CLOSER}
MODEL = "AgentRun"
MODULE = "app.models.agents"
TABLE = AgentRun.__tablename__
COLUMNS = frozenset(column.key for column in sa.inspect(AgentRun).column_attrs)
WRITE_METHODS = {"insert", "update", "delete"}  # the functions of ``sqlalchemy`` and the Table methods
WRITE_FUNCTIONS = WRITE_METHODS | {"pg_insert"}
TABLE_FUNCTIONS = {"table", "Table"}
# The table bare, schema-qualified (``public.agent_runs``) or quoted (``"public"."agent_runs"``).
RAW_SQL = re.compile(
    rf'\b(insert\s+into|update|delete\s+from)\s+(?:(?:"\w+"|\w+)\.)?(?:"{TABLE}"|{TABLE}\b)', re.IGNORECASE
)


def _model_names(tree: ast.Module) -> set[str]:
    classes = {MODEL}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == MODULE:
            classes |= {a.asname or a.name for a in node.names if a.name == MODEL}
    return classes


def _imports_model(tree: ast.Module) -> bool:
    """The module imports the model, or the module that defines it."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (
            (node.module == MODULE and any(a.name == MODEL for a in node.names))
            or (node.module == "app.models" and any(a.name == "agents" for a in node.names))
        ):
            return True
        if isinstance(node, ast.Import) and any(a.name == MODULE for a in node.names):
            return True
    return False


def _write_functions(tree: ast.Module) -> set[str]:
    """``WRITE_FUNCTIONS`` and every local name ``insert``/``update``/``delete`` is imported as from
    ``sqlalchemy`` or one of its modules (``update as upd``, ``postgresql.insert as ins``)."""
    names = set(WRITE_FUNCTIONS)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "sqlalchemy":
            names |= {a.asname or a.name for a in node.names if a.name in WRITE_METHODS}
    return names


def _is_model(node: ast.expr, classes: Set[str]) -> bool:
    """The model or its ``__table__``: by a name it is imported as, a module attribute, or ``getattr`` with a
    name the AST can spell (``getattr(m, "Agent" + "Run")``)."""
    if isinstance(node, ast.Name):
        return node.id in classes
    if isinstance(node, ast.Attribute):
        return node.attr == MODEL or (node.attr == "__table__" and _is_model(node.value, classes))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
    ):
        name = _string_value(node.args[1], classes)
        return name == MODEL or (name == "__table__" and _is_model(node.args[0], classes))
    return False


def _hole(value: str | None) -> str:
    return value if value is not None else "{}"


def _string_value(node: ast.expr, classes: Set[str] = frozenset({MODEL})) -> str | None:
    """The text a string expression builds, as far as the AST spells it: a constant, an f-string,
    concatenation (``+``), ``%`` and ``.format``. The model's ``__tablename__`` reads as the table's name;
    any other value is a ``{}`` hole. None when the node builds no string the AST can tell."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute) and node.attr == "__tablename__" and _is_model(node.value, classes):
        return TABLE
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
            else _hole(_string_value(v.value, classes) if isinstance(v, ast.FormattedValue) else None)
            for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string_value(node.left, classes), _string_value(node.right, classes)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = _string_value(node.left, classes)
        if template is not None:
            return _percent(template, node.right, classes)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        template = _string_value(node.func.value, classes)
        if template is not None:
            args = [_hole(_string_value(a, classes)) for a in node.args]
            kwargs = {k.arg: _hole(_string_value(k.value, classes)) for k in node.keywords if k.arg}
            try:
                return template.format(*args, **kwargs)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                return template
    return None


def _percent(template: str, right: ast.expr, classes: Set[str]) -> str:
    values: tuple[str, ...] | dict[str, str]
    if isinstance(right, ast.Tuple):
        values = tuple(_hole(_string_value(e, classes)) for e in right.elts)
    elif isinstance(right, ast.Dict):
        values = {
            key: _hole(_string_value(v, classes))
            for k, v in zip(right.keys, right.values, strict=True)
            if k is not None and (key := _string_value(k, classes)) is not None
        }
    else:
        values = (_hole(_string_value(right, classes)),)
    try:
        return template % values
    except (KeyError, TypeError, ValueError):
        return template


def _bound(target: ast.expr) -> set[str]:
    """The names a binding target binds: a name, or the names it unpacks into (never the object of an
    attribute or item it stores into)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple | ast.List):
        return {name for element in target.elts for name in _bound(element)}
    if isinstance(target, ast.Starred):
        return _bound(target.value)
    return set()


def _bindings(tree: ast.Module) -> list[tuple[list[ast.expr], ast.expr]]:
    pairs: list[tuple[list[ast.expr], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            pairs.append((node.targets, node.value))
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr) and node.value is not None:
            pairs.append(([node.target], node.value))
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            pairs.append(([node.target], node.iter))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            pairs.append(([node.optional_vars], node.context_expr))
    return pairs


def _mentions(node: ast.AST, classes: Set[str], loaded: Set[str]) -> bool:
    return any(
        isinstance(n, ast.expr) and (_is_model(n, classes) or (isinstance(n, ast.Name) and n.id in loaded))
        for n in ast.walk(node)
    )


def _loaded_names(tree: ast.Module, classes: Set[str]) -> set[str]:
    """Every name the module binds from an expression that names the model or another such name: a row of
    ``get(AgentRun, ...)``, a ``select(AgentRun)``, the ``scalars`` of one, a loop variable over them.
    Flow- and scope-blind on purpose: within a module it may take too many names, never too few."""
    pairs = _bindings(tree)
    loaded: set[str] = set()
    while True:
        grown = {
            name
            for targets, value in pairs
            if _mentions(value, classes, loaded)
            for target in targets
            for name in _bound(target)
        }
        if grown <= loaded:
            return loaded
        loaded |= grown


def _attribute_targets(node: ast.AST) -> list[ast.Attribute]:
    targets: list[ast.expr] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign | ast.AugAssign):
        targets = [node.target]
    found: list[ast.Attribute] = []
    while targets:
        target = targets.pop()
        if isinstance(target, ast.Tuple | ast.List):
            targets.extend(target.elts)
        elif isinstance(target, ast.Starred):
            targets.append(target.value)
        elif isinstance(target, ast.Attribute):
            found.append(target)
    return found


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _table_name(value: str | None) -> str | None:
    return value.replace('"', "").rsplit(".", 1)[-1] if value is not None else None


def run_writes(source: str) -> list[str]:
    tree = ast.parse(source)
    classes = _model_names(tree)
    writers = _write_functions(tree)
    loaded = _loaded_names(tree, classes)
    imports_model = _imports_model(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        for target in _attribute_targets(node):
            if _mentions(target.value, classes, loaded):
                found.append(f"line {line}: writes .{target.attr} of a loaded {MODEL}")
            elif imports_model and target.attr in COLUMNS:
                found.append(f"line {line}: writes .{target.attr} in a module that imports {MODEL}")
        if isinstance(node, ast.Call):
            func = node.func
            name = _call_name(func)
            args = [*node.args, *(k.value for k in node.keywords)]
            if _is_model(func, classes):
                found.append(f"line {line}: builds {MODEL}(...)")
            elif name in writers and node.args and _is_model(node.args[0], classes):
                found.append(f"line {line}: {name}({MODEL})")
            elif isinstance(func, ast.Attribute) and name in WRITE_METHODS and _is_model(func.value, classes):
                found.append(f"line {line}: .{name}() of {MODEL} or its table")
            elif (
                name in TABLE_FUNCTIONS
                and node.args
                and _table_name(_string_value(node.args[0], classes)) == TABLE
            ):
                found.append(f"line {line}: {name}({TABLE!r})")
            elif (
                name is not None
                and (name == "query" or name.startswith("bulk_"))
                and any(_mentions(arg, classes, loaded) for arg in args)
            ):
                found.append(f"line {line}: {name}(...) names {MODEL}")
            elif name == "setattr" and len(node.args) >= 2:
                if _mentions(node.args[0], classes, loaded):
                    found.append(f"line {line}: setattr on a loaded {MODEL}")
                elif imports_model and _string_value(node.args[1], classes) in COLUMNS:
                    found.append(f"line {line}: setattr of a column in a module that imports {MODEL}")
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp | ast.Call):
            value = _string_value(node, classes)
            if value is not None and RAW_SQL.search(value):
                found.append(f"line {line}: raw SQL writes {TABLE}")
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


# AG-13 PoC 3.2c2a (MEDIUM-1, Lead ruling R-F6): fourteen idioms that really write ``agent_runs`` and that the
# first guard did not see, copied from the PoC's sources. Each must be flagged.
_IMPORTS = "from sqlalchemy import update, insert, delete\nfrom app.models.agents import AgentRun\n"
BYPASSES = {
    "aliased update": "from sqlalchemy import update as upd\nfrom app.models.agents import AgentRun\n"
    "q = upd(AgentRun).values(status='failed')\n",
    "aliased insert": "from sqlalchemy.dialects.postgresql import insert as ins\n"
    "from app.models.agents import AgentRun\nq = ins(AgentRun).values(id=i)\n",
    "Table.update()": _IMPORTS + "q = AgentRun.__table__.update().values(status='failed')\n",
    "Table.delete()": _IMPORTS + "q = AgentRun.__table__.delete()\n",
    "ORM attribute write": "from app.models.agents import AgentRun\n"
    "async def f(s, i):\n    run = await s.get(AgentRun, i)\n    run.status = 'failed'\n"
    "    await s.commit()\n",
    "ORM setattr": "from app.models.agents import AgentRun\n"
    "async def f(s, i):\n    run = await s.get(AgentRun, i)\n    setattr(run, 'status', 'failed')\n",
    "legacy Query.update": "from app.models.agents import AgentRun\n"
    "def f(s):\n    s.query(AgentRun).filter(AgentRun.id == 1).update({'status': 'failed'})\n",
    "bulk_update_mappings": "from app.models.agents import AgentRun\n"
    "def f(s):\n    s.bulk_update_mappings(AgentRun, [{'id': 1, 'status': 'failed'}])\n",
    "lightweight table()": "import sqlalchemy as sa\n"
    "q = sa.table('agent_runs', sa.column('status')).update().values(status='failed')\n",
    "raw SQL schema-qualified": "Q = 'UPDATE public.agent_runs SET status = :s'\n",
    "raw SQL quoted ident": "Q = 'UPDATE \"agent_runs\" SET status = :s'\n",
    "raw SQL via tablename f-string": "from app.models.agents import AgentRun\n"
    "Q = f'UPDATE {AgentRun.__tablename__} SET status = :s'\n",
    "raw SQL via format": "Q = 'UPDATE {} SET status = :s'.format('agent_runs')\n",
    "getattr model": "import app.models.agents as m\nfrom sqlalchemy import update\n"
    "q = update(getattr(m, 'Agent' + 'Run'))\n",
}
# The PoC's idioms the first guard already saw: still flagged.
SEEN = {
    "update(AgentRun)": _IMPORTS + "q = update(AgentRun)\n",
    "sa.update(AgentRun)": "import sqlalchemy as sa\nfrom app.models.agents import AgentRun\n"
    "q = sa.update(AgentRun)\n",
    "module attr": "import app.models.agents as m\nfrom sqlalchemy import update\nq = update(m.AgentRun)\n",
    "aliased model": "from app.models.agents import AgentRun as R\nfrom sqlalchemy import update\n"
    "q = update(R)\n",
}
# The same doors, spelled another way (FW4: every way of building a string, the other loaders and writers).
VARIANTS = {
    "aliased delete": "from sqlalchemy import delete as rm\nfrom app.models.agents import AgentRun\n"
    "q = rm(AgentRun)\n",
    "Table.insert()": _IMPORTS + "q = AgentRun.__table__.insert().values(id=i)\n",
    "model .update()": "from app.models.agents import AgentRun\nq = AgentRun.update()\n",
    "Table('agent_runs', ...)": "from sqlalchemy import Table\n"
    "t = Table('agent_runs', md, autoload_with=c)\n",
    "table() of the tablename": "import sqlalchemy as sa\nfrom app.models.agents import AgentRun\n"
    "t = sa.table(AgentRun.__tablename__)\n",
    "bulk_insert_mappings": "from app.models.agents import AgentRun\n"
    "s.bulk_insert_mappings(AgentRun, rows)\n",
    "write on a scalars() row": "from sqlalchemy import select\nfrom app.models.agents import AgentRun\n"
    "async def f(s):\n    rows = (await s.scalars(select(AgentRun))).all()\n    rows[0].status = 'failed'\n",
    "write in a loop over a select": "from sqlalchemy import select\nfrom app.models.agents import AgentRun\n"
    "async def f(s):\n    stmt = select(AgentRun)\n    for run in await s.scalars(stmt):\n"
    "        run.error_code = 'interrupted'\n",
    "augmented write": "from app.models.agents import AgentRun\n"
    "async def f(s, i):\n    run = await s.get(AgentRun, i)\n    run.iterations += 1\n",
    "write on the loaded expression": "from app.models.agents import AgentRun\n"
    "async def f(s, i):\n    (await s.get(AgentRun, i)).status = 'failed'\n",
    "column write in a helper": "from app.models.agents import AgentRun\n"
    "def mark(run):\n    run.finished_at = now\n",
    "column setattr in a helper": "from app.models.agents import AgentRun\n"
    "def mark(run):\n    setattr(run, 'status', 'failed')\n",
    "tablename by %": "from app.models.agents import AgentRun\n"
    "Q = 'DELETE FROM %s WHERE id = :i' % AgentRun.__tablename__\n",
    "tablename by % tuple": "from app.models.agents import AgentRun\n"
    "Q = 'insert into %s (id) values (:i)' % (AgentRun.__tablename__,)\n",
    "tablename by +": "from app.models.agents import AgentRun\n"
    "Q = 'UPDATE ' + AgentRun.__tablename__ + ' SET status = :s'\n",
    "tablename by format": "from app.models.agents import AgentRun\n"
    "Q = 'UPDATE {t} SET status = :s'.format(t=AgentRun.__tablename__)\n",
    "schema and ident quoted": 'Q = \'delete from "public"."agent_runs" where id = :i\'\n',
}
# Reads, and writes of other tables, in a module that names AgentRun: never flagged.
READS = {
    "select": "from sqlalchemy import select\nfrom app.models.agents import AgentRun\nq = select(AgentRun)\n",
    "attribute read": "from app.models.agents import AgentRun\n"
    "async def f(s, i):\n    run = await s.get(AgentRun, i)\n    status = run.status\n    return status\n",
    "another model's row": "from app.models.agents import AgentProfile, AgentRun\n"
    "async def f(s, i):\n    profile = await s.get(AgentProfile, i)\n    profile.enabled = False\n"
    "    setattr(profile, 'in_registry', False)\n",
    "another table": "import sqlalchemy as sa\nq = sa.table('agent_profiles').update()\n",
    "raw SQL read": "Q = 'SELECT status FROM agent_runs WHERE id = :i'\n",
    "raw SQL of another table": "Q = 'UPDATE agent_runs_archive SET status = :s'\n",
    "a column name outside a model module": "def f(job):\n    job.status = 'failed'\n",
}


def test_guard_sees_every_bypass_of_the_poc() -> None:
    missed = [name for name, source in BYPASSES.items() if not run_writes(source)]
    assert missed == []
    assert len(BYPASSES) == 14


def test_guard_still_sees_what_it_saw() -> None:
    assert [name for name, source in SEEN.items() if not run_writes(source)] == []


def test_guard_sees_the_variants() -> None:
    assert [name for name, source in VARIANTS.items() if not run_writes(source)] == []


def test_guard_passes_reads_and_other_tables() -> None:
    assert {name: hits for name, source in READS.items() if (hits := run_writes(source))} == {}
