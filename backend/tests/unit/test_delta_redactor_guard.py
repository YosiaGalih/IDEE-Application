"""One place in app/ builds a ``DeltaRedactor``: ``delta_redactor`` (WP-3.1f2c, Lead ruling on the review of
08fa05e).

A ``DeltaRedactor`` built without ``redact_with_stop`` reads a header value's stop on the redacted text, where
a rule after the header rule may have taken it (review of 45c2ec7: a key's quoted value, an argv item, netrc's
word), and the value then streams raw. ``delta_redactor`` passes the redactor's ``redact_with_stop`` and its
``delta_hold``; the scan checks that the stop is the redactor's own method, not a lambda or a wrapper around
``redact`` (silent-failure review LOW-3, final review of 74bf8d4). Tests may still build one around a fake
redaction. import-linter cannot see which function constructs what, hence this static scan (as
``test_model_call_guard.py``).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
RUNTIME = APP / "agents" / "runtime.py"
TARGET = "DeltaRedactor"
ALLOWED_BUILDER = "delta_redactor"
REQUIRED = ("hold", "redact_with_stop")


@dataclass(frozen=True)
class Site:
    kind: str  # "call" | "ref" (the class passed on, aliased or subclassed: a build the scan cannot follow)
    where: str  # enclosing Class.function, or "<module>"
    line: int
    keywords: tuple[str, ...] = ()  # keywords of a call that are passed and not the constant None
    splat: bool = False  # the call passes **kwargs
    stop_is_the_redactors: bool = False  # redact_with_stop=<p>.redact_with_stop, p the first parameter


def _aliases(tree: ast.Module) -> set[str]:
    names = {TARGET}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname for a in node.names if a.name == TARGET and a.asname}
    return names


def _annotations(tree: ast.Module) -> set[ast.AST]:
    """Every node inside a type annotation: naming the class there builds nothing."""
    roots: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            roots.append(node.returns)
    return {inner for root in roots for inner in ast.walk(root)}


def _where(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    scope: list[str] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            scope.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(scope)) or "<module>"


def _stop_is_the_redactors(call: ast.Call, node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """The call's ``redact_with_stop`` is the ``redact_with_stop`` attribute of the first parameter of the
    function it is in (``delta_redactor(redactor)``: ``redactor.redact_with_stop``), not a lambda, another
    method or another object's (silent-failure review LOW-3)."""
    function = parents.get(node)
    while function is not None and not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
        function = parents.get(function)
    if function is None:
        return False
    params = [*function.args.posonlyargs, *function.args.args]
    stop = next((k.value for k in call.keywords if k.arg == "redact_with_stop"), None)
    return (
        bool(params)
        and isinstance(stop, ast.Attribute)
        and stop.attr == "redact_with_stop"
        and isinstance(stop.value, ast.Name)
        and stop.value.id == params[0].arg
    )


def _is_target(node: ast.expr, names: set[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in names) or (
        isinstance(node, ast.Attribute) and node.attr == TARGET
    )


def builder_sites(source: str) -> list[Site]:
    tree = ast.parse(source)
    names = _aliases(tree)
    typed = _annotations(tree)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    sites: list[Site] = []
    called: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_target(node.func, names):
            called.add(node.func)
            keywords = tuple(
                k.arg
                for k in node.keywords
                if k.arg is not None and not (isinstance(k.value, ast.Constant) and k.value.value is None)
            )
            splat = any(k.arg is None for k in node.keywords)
            stop = _stop_is_the_redactors(node, node, parents)
            sites.append(Site("call", _where(node, parents), node.lineno, keywords, splat, stop))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name | ast.Attribute)
            and node not in called
            and node not in typed
            and _is_target(node, names)
        ):
            sites.append(Site("ref", _where(node, parents), node.lineno))
    return sites


def _violations(path: Path, sites: list[Site]) -> list[str]:
    return [
        f"line {s.line}: {s.kind} in {s.where}"
        for s in sites
        if not (path == RUNTIME and s.kind == "call" and s.where == ALLOWED_BUILDER)
    ]


def _app_sites() -> dict[Path, list[Site]]:
    return {path: builder_sites(path.read_text()) for path in APP.rglob("*.py")}


def test_only_delta_redactor_builds_a_delta_redactor() -> None:
    sites = _app_sites()
    violations = {
        str(path.relative_to(APP)): hits
        for path, found in sites.items()
        if (hits := _violations(path, found))
    }
    assert violations == {}
    calls = [s for found in sites.values() for s in found if s.kind == "call"]
    assert len(calls) == 1
    assert calls[0].where == ALLOWED_BUILDER


def test_the_one_build_passes_the_stop_and_the_hold() -> None:
    (call,) = [s for s in builder_sites(RUNTIME.read_text()) if s.kind == "call"]
    assert set(REQUIRED) <= set(call.keywords)
    assert not call.splat
    assert call.stop_is_the_redactors


GOOD = """
class DeltaRedactor:
    def __init__(self, redact, *, hold=256, release=256, redact_with_stop=None) -> None: ...

def delta_redactor(redactor: Redactor, *, release: int = 256) -> DeltaRedactor:
    return DeltaRedactor(
        redactor.redact,
        hold=delta_hold(redactor),
        release=release,
        redact_with_stop=redactor.redact_with_stop,
    )

class _Run:
    deltas: DeltaRedactor
"""


def test_guard_sees_the_one_builder() -> None:
    sites = builder_sites(GOOD)
    assert [(s.kind, s.where) for s in sites] == [("call", ALLOWED_BUILDER)]
    assert _violations(RUNTIME, sites) == []
    assert set(sites[0].keywords) == {"hold", "release", "redact_with_stop"}


def test_guard_detects_a_second_builder() -> None:
    direct = GOOD + "\ndef start(redactor):\n    return DeltaRedactor(redactor.redact)\n"
    assert _violations(RUNTIME, builder_sites(direct)) == ["line 17: call in start"]
    module = "from app.agents import runtime\ndef f(r):\n    return runtime.DeltaRedactor(r.redact)\n"
    assert _violations(APP / "services" / "x.py", builder_sites(module)) == ["line 3: call in f"]
    aliased = (
        "from app.agents.runtime import DeltaRedactor as Stream\ndef f(r):\n    return Stream(r.redact)\n"
    )
    assert _violations(APP / "services" / "x.py", builder_sites(aliased)) == ["line 3: call in f"]
    copied = GOOD.replace("def delta_redactor(", "def delta_redactor_copy(")
    assert _violations(RUNTIME, builder_sites(copied)) == ["line 6: call in delta_redactor_copy"]


def test_guard_detects_the_class_passed_on() -> None:
    for source in (
        "MAKE = DeltaRedactor\n",
        "class Quiet(DeltaRedactor):\n    pass\n",
        "deltas = functools.partial(DeltaRedactor, redact)\n",
        "deltas = (lambda make: make(redact))(DeltaRedactor)\n",
    ):
        assert [s.kind for s in builder_sites(source)] == ["ref"], source


def test_guard_detects_a_build_without_the_stop() -> None:
    for call in (
        "DeltaRedactor(r.redact, hold=h)",
        "DeltaRedactor(r.redact, hold=h, redact_with_stop=None)",
        "DeltaRedactor(r.redact, **options)",
    ):
        (site,) = builder_sites(f"def delta_redactor(r):\n    return {call}\n")
        assert not set(REQUIRED) <= set(site.keywords) or site.splat, call


def test_guard_detects_a_stop_that_is_not_the_redactors() -> None:
    """Silent-failure review LOW-3 (final review of 74bf8d4): the stop is the redactor's own
    ``redact_with_stop``, the attribute of that name of the redactor passed to ``delta_redactor``: not a
    lambda, another method, another object's, or a wrapper."""
    assert builder_sites(GOOD)[0].stop_is_the_redactors
    for stop in (
        "lambda text: (redactor.redact(text), None)",
        "redactor.redact",
        "other.redact_with_stop",
        "redactor.inner.redact_with_stop",
        "functools.partial(redactor.redact_with_stop)",
        "redact_with_stop",
    ):
        source = GOOD.replace("redact_with_stop=redactor.redact_with_stop", f"redact_with_stop={stop}")
        (site,) = [s for s in builder_sites(source) if s.kind == "call"]
        assert not site.stop_is_the_redactors, stop
    renamed = GOOD.replace("redactor", "r").replace("delta_r(", "delta_redactor(")
    assert [s.stop_is_the_redactors for s in builder_sites(renamed) if s.kind == "call"] == [True]


def test_guard_ignores_annotations() -> None:
    source = "def f(d: DeltaRedactor) -> DeltaRedactor:\n    x: DeltaRedactor = d\n    return x\n"
    assert builder_sites(source) == []
