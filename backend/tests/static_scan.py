"""The static scan helpers the writer guards share (BC-19; moved from test_agent_run_writer_guard.py).

``string_value`` reads the text a string expression builds as far as the AST spells it, with ``{}`` for a
piece it cannot read (the tripwire, the status guard's dynamic-column regexes and ``_constant_names`` depend
on that placeholder). ``normalise_sql`` reads SQL the way a matcher needs it: no comments, one space between
words, none around ``.``, no quotes, ``U&"..."`` identifiers unescaped. ``bindings`` and ``bound`` list what
a module binds to which names. The model-specific part, the name a ``__tablename__`` attribute reads as, is
the optional ``table_names`` argument; by default ``AgentRun.__tablename__`` reads as ``agent_runs``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator

TableNames = Callable[[ast.expr], str | None]


def _default_table_names(node: ast.expr) -> str | None:
    """``AgentRun`` (a name or an attribute) is the model whose ``__tablename__`` reads as ``agent_runs``."""
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
    return "agent_runs" if name == "AgentRun" else None


# A literal or quoted name comes first, so ``--`` or ``/*`` inside one is not taken for a comment (review T7):
# E-strings (backslash escapes) and dollar-quoted strings before the plain ones (AG-13 LOW-2).
LEXEME = re.compile(
    r"\b[eE]'(?:[^'\\]|\\.|'')*'|\$(\w*)\$.*?\$\1\$|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/",
    re.DOTALL,
)
UNICODE_IDENT = re.compile(r'\bU&"((?:[^"]|"")*)"', re.IGNORECASE)
UNICODE_ESCAPE = re.compile(r"\\(?:\+([0-9a-fA-F]{6})|([0-9a-fA-F]{4}))")
AROUND_DOT = re.compile(r"\s*\.\s*")
QUOTES = re.compile(r"[\"'`]")


def unescape_unicode(name: str) -> str:
    """``U&"\\0061b"`` (the default escape character) reads as ``"ab"``."""
    return UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1) or m.group(2), 16)), name)


def normalise_sql(text: str) -> str:
    """The SQL as the matcher reads it: ``/*...*/`` and ``--...`` comments (each a space), whitespace
    collapsed, no space around ``.``, no quotes (``"``, ``'``, backtick)."""
    text = LEXEME.sub(lambda m: " " if m.group().startswith(("--", "/*")) else m.group(), text)
    text = UNICODE_IDENT.sub(lambda m: f'"{unescape_unicode(m.group(1))}"', text)
    return QUOTES.sub("", AROUND_DOT.sub(".", " ".join(text.split())))


def hole(value: str | None) -> str:
    return value if value is not None else "{}"


def string_value(node: ast.expr, *, table_names: TableNames | None = None) -> str | None:
    """The text a string expression builds, as far as the AST spells it: a constant, an f-string,
    concatenation (``+``), ``%`` and ``.format``. The model's ``__tablename__`` reads as the table's name;
    any other value is a ``{}`` hole. None when the node builds no string the AST can tell."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Attribute) and node.attr == "__tablename__":
        if (table := (table_names or _default_table_names)(node.value)) is not None:
            return table
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
            else hole(
                string_value(v.value, table_names=table_names) if isinstance(v, ast.FormattedValue) else None
            )
            for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = (
            string_value(node.left, table_names=table_names),
            string_value(node.right, table_names=table_names),
        )
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template = string_value(node.left, table_names=table_names)
        if template is not None:
            return percent(template, node.right, table_names=table_names)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.List | ast.Tuple)
    ):  # ``" ".join([...])``
        sep = string_value(node.func.value, table_names=table_names)
        parts = [string_value(e, table_names=table_names) for e in node.args[0].elts]
        if sep is not None and any(p is not None for p in parts):  # an unreadable part reads as ``{}``
            return sep.join(hole(p) for p in parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        template = string_value(node.func.value, table_names=table_names)
        if template is not None:
            args = [hole(string_value(a, table_names=table_names)) for a in node.args]
            kwargs = {
                k.arg: hole(string_value(k.value, table_names=table_names)) for k in node.keywords if k.arg
            }
            try:
                return template.format(*args, **kwargs)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                return template
    return None


def paired_names(target: ast.expr, value: ast.expr) -> Iterator[tuple[str, ast.expr]]:
    """(name, value) for ``NAME = value``; a tuple target pairs with a tuple value (``N, M = 'a', 'b'``)."""
    if isinstance(target, ast.Tuple | ast.List) and isinstance(value, ast.Tuple | ast.List):
        if len(target.elts) == len(value.elts):
            for t, v in zip(target.elts, value.elts, strict=True):
                yield from paired_names(t, v)
    elif isinstance(target, ast.Name):
        yield target.id, value


def concat_text(node: ast.expr) -> str | None:
    """``'SET LOCAL ' + n + ' = 1'``: a ``+`` chain with a piece the scan cannot read (it reads as ``{}``)
    when at least one piece is a string; None otherwise."""
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
        return None
    pieces: list[str | None] = []

    def flatten(n: ast.expr) -> None:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            flatten(n.left)
            flatten(n.right)
        else:
            pieces.append(string_value(n))

    flatten(node)
    return "".join("{}" if p is None else p for p in pieces) if any(p is not None for p in pieces) else None


def percent(template: str, right: ast.expr, *, table_names: TableNames | None) -> str:
    values: tuple[str, ...] | dict[str, str]
    if isinstance(right, ast.Tuple):
        values = tuple(hole(string_value(e, table_names=table_names)) for e in right.elts)
    elif isinstance(right, ast.Dict):
        values = {
            key: hole(string_value(v, table_names=table_names))
            for k, v in zip(right.keys, right.values, strict=True)
            if k is not None and (key := string_value(k, table_names=table_names)) is not None
        }
    else:
        values = (hole(string_value(right, table_names=table_names)),)
    try:
        return template % values
    except (KeyError, TypeError, ValueError):
        return template


def bound(target: ast.expr) -> set[str]:
    """The names a binding target binds: a name, or the names it unpacks into (never the object of an
    attribute or item it stores into)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Tuple | ast.List):
        return {name for element in target.elts for name in bound(element)}
    if isinstance(target, ast.Starred):
        return bound(target.value)
    return set()


def bindings(tree: ast.Module) -> list[tuple[list[ast.expr], ast.expr]]:
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
