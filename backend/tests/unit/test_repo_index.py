"""Symbol rules and search helpers (WP-2.1d; ``app/workspace/index.py``)."""

from __future__ import annotations

import pytest

from app.workspace.index import Symbol, check_glob, check_regex, classify, glob_matches, language_for, preview

DEFINITIONS = [
    ("a.py", "def main", Symbol("main", "function")),
    ("a.py", "    async def run", Symbol("run", "function")),
    ("a.py", "class Service", Symbol("Service", "class")),
    ("a.js", "function load", Symbol("load", "function")),
    ("a.js", "export default async function* stream", Symbol("stream", "function")),
    ("a.js", "export class Store", Symbol("Store", "class")),
    ("a.jsx", "const App = (", Symbol("App", "function")),
    ("a.ts", "export interface Props", Symbol("Props", "interface")),
    ("a.ts", "export type Id =", Symbol("Id", "type")),
    ("a.ts", "export const enum Color", Symbol("Color", "enum")),
    ("a.ts", "export const handler = async (", Symbol("handler", "function")),
    ("a.tsx", "export abstract class Base", Symbol("Base", "class")),
    ("A.java", "public final class Main", Symbol("Main", "class")),
    ("A.java", "interface Repo", Symbol("Repo", "interface")),
    ("A.java", "public enum Level", Symbol("Level", "enum")),
    ("A.java", "record Point", Symbol("Point", "class")),
    ("a.go", "func main", Symbol("main", "function")),
    ("a.go", "func (s *Srv) Serve", Symbol("Serve", "function")),
    ("a.go", "type Config", Symbol("Config", "type")),
    ("a.go", "type Alias =", Symbol("Alias", "type")),  # git prints the longest match: the TS "type X =" one
    ("a.rs", "pub(crate) struct Cfg", Symbol("Cfg", "struct")),
    ("a.rs", "pub async fn serve", Symbol("serve", "function")),
    ("a.rs", "fn main", Symbol("main", "function")),
    ("a.rs", "enum State", Symbol("State", "enum")),
    ("a.rs", "pub trait Store", Symbol("Store", "trait")),
    ("a.rb", "  def save!", Symbol("save!", "function")),
    ("a.rb", "def self.build", Symbol("build", "function")),
    ("a.rb", "class Invoice", Symbol("Invoice", "class")),
    ("a.rb", "module Billing", Symbol("Billing", "module")),
    ("a.php", "public static function boot", Symbol("boot", "function")),
    ("a.php", "final class Kernel", Symbol("Kernel", "class")),
    ("a.php", "trait Loggable", Symbol("Loggable", "trait")),
    ("a.php", "interface Cache", Symbol("Cache", "interface")),
]


@pytest.mark.parametrize(("path", "text", "expected"), DEFINITIONS)
def test_classify_finds_definitions(path: str, text: str, expected: Symbol) -> None:
    assert classify(path, text) == expected


@pytest.mark.parametrize(
    ("path", "text"),
    [
        ("a.py", "function f"),  # JavaScript syntax in a Python file
        ("a.go", "def x"),
        ("README.md", "def x"),  # no symbols for markdown
        ("a.py", "define x"),
        ("a.ts", "type Id"),  # no "=": not a type alias
        ("Makefile", "class X"),
        ("noext", "def x"),
    ],
)
def test_classify_ignores_other_languages_and_near_misses(path: str, text: str) -> None:
    assert classify(path, text) is None


def test_long_symbol_names_are_cut() -> None:
    symbol = classify("a.py", "def " + "n" * 301)
    assert symbol is not None
    assert len(symbol.name) == 300


def test_language_for_moved_unchanged() -> None:
    assert (language_for("src/app.py"), language_for("x.tsx"), language_for("notes")) == (
        "python",
        "typescript",
        None,
    )


@pytest.mark.parametrize(
    ("glob", "path", "expected"),
    [
        ("*.py", "src/a.py", True),
        ("*.py", "a.py", True),
        ("*.py", "a.txt", False),
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "lib/a.py", False),
        ("**/test_*.py", "a/b/test_x.py", True),
        ("**/test_*.py", "test_x.py", True),
        ("docs/**", "docs/a/b.md", True),
        ("*.PY", "a.py", False),  # case-sensitive, like git
    ],
)
def test_glob_matches(glob: str, path: str, expected: bool) -> None:
    assert glob_matches(glob, path) is expected


@pytest.mark.parametrize("glob", ["x" * 257, "*" * 9, "a\0b", "a\nb"])
def test_check_glob_limits(glob: str) -> None:
    with pytest.raises(ValueError):
        check_glob(glob)


def test_check_glob_accepts_ordinary_globs() -> None:
    check_glob("src/**/*.py")


def test_preview_windows_long_lines() -> None:
    line = "a" * 5000 + "NEEDLE" + "b" * 5000
    shown = preview(line, 5001)
    assert len(shown) <= 200
    assert "NEEDLE" in shown


def test_preview_keeps_short_lines_and_drops_trailing_space() -> None:
    assert preview("    x = 1   ", 5) == "    x = 1"
    assert preview("short", None) == "short"


@pytest.mark.parametrize("q", [r"(a)\1", r"x\9", "a{2}", "(a{1,9}){1,9}"])
def test_regex_features_that_explode_in_glibc_are_refused(q: str) -> None:
    with pytest.raises(ValueError):
        check_regex(q)


@pytest.mark.parametrize("q", [r"a\{b", r"\\1", "a+b*", r"\.py$", "x|y"])
def test_ordinary_regexes_pass(q: str) -> None:
    check_regex(q)
