"""One model-call path in app/ (spec WP-3.1f Keputusan Lead #17; silent-failure M3; AG-13 3.1f1 L1).

``create_with_retry`` is called once, from ``AgentRuntime._call_model``, always with ``before_attempt``
(lease, provider gate, budget) and ``on_attempt`` (the ledger). ``provider.create_message`` is called only by
the retry loop and by the readiness runner (3.1e1), which calls the provider directly by design.
import-linter cannot see which function calls what, hence this static scan.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
RETRY = APP / "llm" / "retry.py"
RUNTIME = APP / "agents" / "runtime.py"
READINESS = APP / "llm" / "readiness.py"
TARGET = "create_with_retry"
ALLOWED_CALLER = "AgentRuntime._call_model"
HOOKS = ("before_attempt", "on_attempt")


@dataclass(frozen=True)
class Site:
    kind: str  # "import" | "call" | "ref"
    where: str  # enclosing Class.function, or "<module>"
    line: int
    hooks: tuple[str, ...] = ()  # keywords of a call that are passed and not the constant None
    splat: bool = False  # the call passes **kwargs


def _aliases(tree: ast.Module) -> set[str]:
    names = {TARGET}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname for a in node.names if a.name == TARGET and a.asname}
    return names


def _where(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    scope: list[str] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            scope.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(scope)) or "<module>"


def _is_target(node: ast.expr, names: set[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in names) or (
        isinstance(node, ast.Attribute) and node.attr == TARGET
    )


def model_call_sites(source: str) -> list[Site]:
    tree = ast.parse(source)
    names = _aliases(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    sites: list[Site] = []
    called: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == TARGET for a in node.names):
            sites.append(Site("import", _where(node, parents), node.lineno))
        elif isinstance(node, ast.Call) and _is_target(node.func, names):
            called.add(node.func)
            hooks = tuple(
                k.arg
                for k in node.keywords
                if k.arg in HOOKS and not (isinstance(k.value, ast.Constant) and k.value.value is None)
            )
            splat = any(k.arg is None for k in node.keywords)
            sites.append(Site("call", _where(node, parents), node.lineno, hooks, splat))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute) and node not in called and _is_target(node, names):
            sites.append(Site("ref", _where(node, parents), node.lineno))
    return sites


def create_message_calls(source: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_message"
    ]


def _violations(path: Path, sites: list[Site]) -> list[str]:
    if path == RUNTIME:
        return [
            f"line {s.line}: {s.kind} in {s.where}"
            for s in sites
            if not (s.kind == "import" and s.where == "<module>") and s.where != ALLOWED_CALLER
        ]
    return [f"line {s.line}: {s.kind} in {s.where}" for s in sites]


def _app_sites() -> dict[Path, list[Site]]:
    return {path: model_call_sites(path.read_text()) for path in APP.rglob("*.py") if path != RETRY}


def test_only_the_runtime_calls_create_with_retry() -> None:
    sites = _app_sites()
    violations = {
        str(path.relative_to(APP)): hits
        for path, found in sites.items()
        if (hits := _violations(path, found))
    }
    assert violations == {}
    calls = [s for found in sites.values() for s in found if s.kind == "call"]
    assert len(calls) == 1
    assert calls[0].where == ALLOWED_CALLER


def test_the_one_call_passes_both_hooks() -> None:
    (call,) = [s for s in model_call_sites(RUNTIME.read_text()) if s.kind == "call"]
    assert set(call.hooks) == set(HOOKS)
    assert not call.splat


def test_only_retry_and_readiness_call_create_message() -> None:
    callers = {
        str(path.relative_to(APP)) for path in APP.rglob("*.py") if create_message_calls(path.read_text())
    }
    assert callers == {"llm/retry.py", "llm/readiness.py"}


GOOD = """
from app.llm.retry import create_with_retry

class AgentRuntime:
    async def _call_model(self, call, provider, *, cancel):
        return await create_with_retry(provider, call.request, before_attempt=check, on_attempt=record)
"""


def test_guard_sees_the_allowed_caller() -> None:
    sites = model_call_sites(GOOD)
    assert [s.kind for s in sites] == ["import", "call"]
    assert _violations(RUNTIME, sites) == []
    assert set(sites[1].hooks) == set(HOOKS)
    assert len([s for s in model_call_sites(RUNTIME.read_text()) if s.kind == "call"]) == 1


def test_guard_detects_a_second_caller() -> None:
    second = GOOD + "\nasync def shortcut(p, r):\n    return await create_with_retry(p, r)\n"
    assert _violations(RUNTIME, model_call_sites(second)) == ["line 9: call in shortcut"]
    other = "from app.llm import retry\nasync def f(p, r):\n    return await retry.create_with_retry(p, r)\n"
    assert _violations(APP / "services" / "x.py", model_call_sites(other)) == ["line 3: call in f"]
    passed_on = "from app.llm.retry import create_with_retry\nHANDLER = create_with_retry\n"
    assert {s.kind for s in model_call_sites(passed_on)} == {"import", "ref"}


def test_guard_detects_a_call_without_a_hook() -> None:
    for call in (
        "create_with_retry(provider, req, on_attempt=record)",
        "create_with_retry(provider, req, before_attempt=check, on_attempt=None)",
        "create_with_retry(provider, req, **hooks)",
    ):
        source = f"from app.llm.retry import create_with_retry\nasync def f():\n    await {call}\n"
        (site,) = [s for s in model_call_sites(source) if s.kind == "call"]
        assert set(site.hooks) != set(HOOKS) or site.splat


def test_guard_detects_an_aliased_import() -> None:
    source = (
        "from app.llm.retry import create_with_retry as cwr\n"
        "async def f(p, r):\n    return await cwr(p, r, before_attempt=a, on_attempt=b)\n"
    )
    assert [s.kind for s in model_call_sites(source)] == ["import", "call"]
    assert _violations(APP / "services" / "x.py", model_call_sites(source)) == [
        "line 1: import in <module>",
        "line 3: call in f",
    ]


def test_guard_detects_a_direct_create_message() -> None:
    assert create_message_calls("async def f(p, r):\n    return await p.create_message(r)\n") == [2]
