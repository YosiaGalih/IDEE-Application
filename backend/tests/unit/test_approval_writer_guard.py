"""Who may decide and who may write an approval's status (CLAUDE.md #5; 3.1c1 AG-13 C3, spec WP-3.1d Goal 6).

A static scan of app/: only ``app/services/approvals.py`` (a signed-in person with the permissions) calls
``approval_store.decide``; only ``approval_store`` (and the model that names it) touches the writer marker the
``approvals_status_guard`` trigger requires. The trigger itself is tested in test_tool_tables.py.

Closed by the hardening owed before 3.2d (AG-13 3.2c2c1 delta; spec §16.6): the marker text is matched without
regard to case (``'kk.Approval_Status_Writer'``, ``SET LOCAL kk.APPROVAL_STATUS_WRITER``), also when it is
joined from pieces by ``+``, an f-string, ``%``, ``.format`` or ``join``; a name split inside the word by SQL
``||``, ``concat`` or ``U&`` is caught by the GUC tripwire (``test_guc_tripwire.py``), which flags every
way to set a GUC in ``app/`` outside the exact ``GUC_WRITERS`` allowlist.

Still out of scope, accepted: a ``decide`` or marker name computed at run time, ``operator.methodcaller``,
``inspect.getmembers``, ``getattr`` aliases for ``decide`` (only the direct ``getattr(x, "decide")`` is read),
``ALTER ROLE|DATABASE ... SET``, ``sys.modules`` / ``globals()`` lookups, and an indirect writer through a
module of the allowlist (anything that calls ``approvals.decide``). The DB backstop is a backlog item.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

from tests.unit.test_agent_run_writer_guard import _string_value
from tests.unit.test_guc_tripwire import violations

APP = Path(__file__).resolve().parents[2] / "app"
DECIDERS = {APP / "services" / "approvals.py"}
MARKER_OWNERS = {APP / "services" / "approval_store.py", APP / "models" / "approvals.py"}
MARKER_NAMES = {"APPROVAL_WRITER", "APPROVAL_WRITER_SETTING"}
MARKER_TEXT = "kk.approval_status_writer"


def _store_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names bound to the approval_store module, and names bound to its ``decide`` function."""
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.services":
            modules |= {a.asname or a.name for a in node.names if a.name == "approval_store"}
        if isinstance(node, ast.ImportFrom) and node.module == "app.services.approval_store":
            functions |= {a.asname or a.name for a in node.names if a.name == "decide"}
            if any(a.name == "*" for a in node.names):
                functions.add("decide")
        if isinstance(node, ast.Import):
            modules |= {a.asname or a.name for a in node.names if a.name == "app.services.approval_store"}
    return modules, functions


def decide_calls(source: str) -> list[int]:
    tree = ast.parse(source)
    modules, functions = _store_aliases(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in functions:
            lines.append(node.lineno)
        if isinstance(func, ast.Attribute) and func.attr == "decide":
            receiver = ast.unparse(func.value)
            if receiver in modules or receiver.endswith("approval_store"):
                lines.append(node.lineno)
        # getattr(approval_store, "decide") reaches it too
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
            name = node.args[1]
            if isinstance(name, ast.Constant) and name.value == "decide":
                lines.append(node.lineno)
    return lines


def _inside_string_expression(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """Report an f-string, ``+``, ``%``, ``.format`` or ``join`` once, at its outermost node."""
    parent = parents.get(node)
    while isinstance(parent, ast.expr):
        if isinstance(parent, ast.JoinedStr | ast.BinOp | ast.Call) and _string_value(parent) is not None:
            return True
        parent = parents.get(parent)
    return False


def marker_uses(source: str) -> list[int]:
    """Uses of the marker. The text is matched in lower case (PostgreSQL reads a GUC name without regard to
    case); a name split by SQL ``||``, ``concat`` or ``U&`` is the GUC tripwire's (test_guc_tripwire.py)."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp | ast.Call):
            value = _string_value(node)
            if (
                value is not None
                and MARKER_TEXT in value.lower()
                and not _inside_string_expression(node, parents)
            ):
                lines.append(node.lineno)
        if isinstance(node, ast.alias) and node.name in MARKER_NAMES:
            lines.append(getattr(node, "lineno", 0))
        if isinstance(node, ast.Attribute) and node.attr in MARKER_NAMES:
            lines.append(node.lineno)
    return lines


def _scan(check: Callable[[str], list[int]], allowed: set[Path]) -> dict[str, list[int]]:
    return {
        str(path.relative_to(APP)): hits
        for path in APP.rglob("*.py")
        if path not in allowed and (hits := check(path.read_text()))
    }


def test_only_the_approvals_service_decides() -> None:
    assert _scan(decide_calls, DECIDERS) == {}


def test_only_approval_store_holds_the_writer_marker() -> None:
    assert _scan(marker_uses, MARKER_OWNERS) == {}


def test_the_scans_see_the_allowed_code() -> None:
    # Sanity: each scan notices the real call or use when not exempt.
    assert decide_calls((APP / "services" / "approvals.py").read_text())
    assert marker_uses((APP / "services" / "approval_store.py").read_text())


def test_decide_call_forms_are_detected() -> None:
    sources = [
        "from app.services import approval_store\nawait approval_store.decide(s, a, x)\n",
        "from app.services import approval_store as st\nawait st.decide(s, a, x)\n",
        "import app.services.approval_store\nawait app.services.approval_store.decide(s)\n",
        "from app.services.approval_store import decide\nawait decide(s)\n",
        "from app.services.approval_store import decide as d\nawait d(s)\n",
        "from app.services import approval_store\nf = getattr(approval_store, 'decide')\n",
    ]
    assert all(decide_calls(source) for source in sources)


def test_other_decide_methods_are_not_flagged() -> None:
    assert decide_calls("async def f(policy):\n    return await policy.decide(1)\n") == []


def test_marker_forms_are_detected() -> None:
    sources = [
        "from app.models.approvals import APPROVAL_WRITER\n",
        "import app.models.approvals as m\nx = m.APPROVAL_WRITER_SETTING\n",
        "q = \"SELECT set_config('kk.approval_status_writer', 'approval_store', true)\"\n",
        # AG-13 delta: PostgreSQL reads a GUC name without regard to case; pieces the AST can join.
        "q = \"SELECT set_config('kk.Approval_Status_Writer', 'approval_store', true)\"\n",
        "q = \"SET LOCAL kk.APPROVAL_STATUS_WRITER = 'approval_store'\"\n",
        "m = 'KK.' + 'Approval_Status_Writer'\n",
        "m = f\"kk.{'approval'}_status_writer\"\n",
        "m = 'kk.%s_status_writer' % 'approval'\n",
        "m = 'kk.approval{}'.format('_status_writer')\n",
        "m = '_'.join(['kk.approval', 'status', 'writer'])\n",
    ]
    assert all(marker_uses(source) for source in sources)


def test_other_spellings_of_the_marker_are_caught_by_the_tripwire() -> None:
    """A name split inside the word, by SQL ``||``, ``concat`` or ``U&``, cannot be read from the text: the
    GUC tripwire flags the call that sets it (test_guc_tripwire.py scans ``app/``; here, these forms)."""
    sources = [
        "q = \"SELECT set_config('kk.approval' || '_status_writer', 'approval_store', true)\"\n",
        "q = \"SELECT set_config(concat('kk.approval', '_status_writer'), 'approval_store', true)\"\n",
        "q = \"SELECT set_config(U&'kk.approval_status\\\\005fwriter', 'approval_store', true)\"\n",
        "q = \"SELECT SET_CONFIG('kk.Approval_Status_Writer', 'approval_store', true)\"\n",
        "q = \"SET SESSION kk.Approval_Status_Writer TO 'approval_store'\"\n",
    ]
    for source in sources:
        assert violations("services/other.py", source), source
        # and not even the approval store may set it through a name the scan cannot read
        if "U&" in source or "||" in source or "concat" in source:
            assert violations("services/approval_store.py", source), source


def test_the_marker_scan_ignores_docs_that_name_the_guard() -> None:
    sources = [
        '"""see tests/unit/test_approval_writer_guard.py"""\n',
        "x = 'approval_status_writer_guard'\n",
        "q = 'UPDATE approvals SET status = :s'\n",
    ]
    assert not any(marker_uses(source) for source in sources)
