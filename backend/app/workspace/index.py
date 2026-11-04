"""Languages, symbol rules and search helpers for the repository index (WP-2.1d; ARCHITECTURE M04).

Framework-free. The index job asks git for every line that starts a definition (``SYMBOL_PATTERNS``, POSIX
ERE, matched by ``git grep -o`` inside a git process the adapter can kill) and ``classify`` decides, from the
file's language, whether the matched text is a definition and of what. The rules are line heuristics, not a
parser: AST/ctags indexing is v2.1 (``index.symbols_incremental``, in the sandbox).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

SYMBOL_MAX_CHARS = 300  # repo_index_entries.symbol varchar(300)
GLOB_MAX_CHARS = 256
GLOB_MAX_STARS = 8
PREVIEW_CHARS = 200
_PREVIEW_LEAD = 60  # characters kept before the match when a long line is windowed

# Monaco language ids by extension (FR-027); unknown → None (plain text).
_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sh": "shell",
    ".sql": "sql",
    ".xml": "xml",
    ".txt": "plaintext",
}
_LANGUAGE_NAMES = {"Dockerfile": "dockerfile", "Makefile": "makefile"}


def language_for(path: str) -> str | None:
    """The editor language for ``path`` (by name, then extension), ``None`` when unknown."""
    name = PurePosixPath(path).name
    if name in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[name]
    return _LANGUAGES.get(PurePosixPath(name).suffix.lower())


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str  # function | class | interface | type | enum | struct | trait | module


# Identifier classes: ASCII, the same in ERE and Python.
_ID = "[A-Za-z_][A-Za-z0-9_]*"
_JS_ID = "[A-Za-z_$][A-Za-z0-9_$]*"
_S = "[[:space:]]"  # ERE whitespace; Python uses \s


@dataclass(frozen=True)
class _Rule:
    languages: frozenset[str]
    ere: str  # for git grep, anchored at the line start
    python: re.Pattern[str]  # fullmatch on the text git printed; group "name", optional group "kind"
    kind: str | None  # fixed kind, else the "kind" group


def _rule(languages: str, ere: str, python: str, kind: str | None = None) -> _Rule:
    return _Rule(frozenset(languages.split()), "^" + ere, re.compile(python), kind)


_RULES = (
    _rule(
        "python", f"{_S}*(async{_S}+)?def{_S}+{_ID}", rf"\s*(?:async\s+)?def\s+(?P<name>{_ID})", "function"
    ),
    _rule("python", f"{_S}*class{_S}+{_ID}", rf"\s*class\s+(?P<name>{_ID})", "class"),
    _rule(
        "javascript typescript",
        f"{_S}*(export{_S}+)?(default{_S}+)?(async{_S}+)?function{_S}*[*]?{_S}*{_JS_ID}",
        rf"\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>{_JS_ID})",
        "function",
    ),
    _rule(
        "javascript typescript",
        f"{_S}*(export{_S}+)?(default{_S}+)?(abstract{_S}+)?class{_S}+{_JS_ID}",
        rf"\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>{_JS_ID})",
        "class",
    ),
    _rule(
        "typescript",
        f"{_S}*(export{_S}+)?interface{_S}+{_JS_ID}",
        rf"\s*(?:export\s+)?interface\s+(?P<name>{_JS_ID})",
        "interface",
    ),
    _rule(
        "typescript",
        f"{_S}*(export{_S}+)?type{_S}+{_JS_ID}{_S}*=",
        rf"\s*(?:export\s+)?type\s+(?P<name>{_JS_ID})\s*=",
        "type",
    ),
    _rule(
        "typescript",
        f"{_S}*(export{_S}+)?(const{_S}+)?enum{_S}+{_JS_ID}",
        rf"\s*(?:export\s+)?(?:const\s+)?enum\s+(?P<name>{_JS_ID})",
        "enum",
    ),
    _rule(
        "javascript typescript",
        f"{_S}*(export{_S}+)?(const|let|var){_S}+{_JS_ID}{_S}*={_S}*(async{_S}*)?(function|[(])",
        rf"\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>{_JS_ID})\s*=\s*(?:async\s*)?(?:function|\()",
        "function",
    ),
    _rule(
        "java",
        f"{_S}*((public|private|protected|static|final|abstract|sealed){_S}+)*(class|interface|enum|record){_S}+{_ID}",
        rf"\s*(?:(?:public|private|protected|static|final|abstract|sealed)\s+)*(?P<kind>class|interface|enum|record)\s+(?P<name>{_ID})",
    ),
    _rule(
        "go",
        f"func{_S}+([(][^)]*[)]{_S}*)?{_ID}",
        rf"func\s+(?:\([^)]*\)\s*)?(?P<name>{_ID})",
        "function",
    ),
    # "type X = Y" (alias): git prints the longest match, the TypeScript "type X =" pattern; accept the "=".
    _rule("go", f"type{_S}+{_ID}", rf"type\s+(?P<name>{_ID})(?:\s*=)?", "type"),
    _rule(
        "rust",
        f"{_S}*(pub([(][^)]*[)])?{_S}+)?((async|const|unsafe){_S}+)*fn{_S}+{_ID}",
        rf"\s*(?:pub(?:\([^)]*\))?\s+)?(?:(?:async|const|unsafe)\s+)*fn\s+(?P<name>{_ID})",
        "function",
    ),
    _rule(
        "rust",
        f"{_S}*(pub([(][^)]*[)])?{_S}+)?(struct|enum|trait){_S}+{_ID}",
        rf"\s*(?:pub(?:\([^)]*\))?\s+)?(?P<kind>struct|enum|trait)\s+(?P<name>{_ID})",
    ),
    _rule(
        "ruby",
        f"{_S}*def{_S}+(self[.])?{_ID}[?!=]?",
        rf"\s*def\s+(?:self\.)?(?P<name>{_ID}[?!=]?)",
        "function",
    ),
    _rule(
        "ruby",
        f"{_S}*(class|module){_S}+[A-Z][A-Za-z0-9_]*",
        r"\s*(?P<kind>class|module)\s+(?P<name>[A-Z][A-Za-z0-9_]*)",
    ),
    _rule(
        "php",
        f"{_S}*((public|private|protected|static|final|abstract){_S}+)*function{_S}+&?{_ID}",
        rf"\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+&?(?P<name>{_ID})",
        "function",
    ),
    _rule(
        "php",
        f"{_S}*((final|abstract|readonly){_S}+)*(class|interface|trait|enum){_S}+{_ID}",
        rf"\s*(?:(?:final|abstract|readonly)\s+)*(?P<kind>class|interface|trait|enum)\s+(?P<name>{_ID})",
    ),
)

# One git grep reads every pattern at once; classify() keeps only what fits the file's language.
SYMBOL_PATTERNS: tuple[str, ...] = tuple(dict.fromkeys(rule.ere for rule in _RULES))
_KIND_ALIASES = {"record": "class"}


def classify(path: str, text: str) -> Symbol | None:
    """The definition ``text`` (a line start printed by ``git grep -o``) makes in ``path``, if any."""
    language = language_for(path)
    for rule in _RULES:
        if language not in rule.languages:
            continue
        match = rule.python.fullmatch(text)
        if match is None:
            continue
        kind = rule.kind or match.group("kind")
        return Symbol(match.group("name")[:SYMBOL_MAX_CHARS], _KIND_ALIASES.get(kind, kind))
    return None


def check_glob(glob: str) -> None:
    """Refuse globs whose matching cost or meaning is not bounded (``ValueError``)."""
    if len(glob) > GLOB_MAX_CHARS or glob.count("*") > GLOB_MAX_STARS:
        raise ValueError("glob_too_complex")
    if any(ord(char) < 32 for char in glob):
        raise ValueError("glob_control_character")


def glob_matches(glob: str, path: str) -> bool:
    """Without ``/`` the glob names files (``*.py`` anywhere); with ``/`` it matches the whole path, where
    ``*`` also crosses ``/`` and a leading ``**/`` may match nothing. Case-sensitive, like git."""
    if "/" not in glob:
        return fnmatchcase(PurePosixPath(path).name, glob)
    if fnmatchcase(path, glob):
        return True
    return glob.startswith("**/") and fnmatchcase(path, glob[3:])


def preview(text: str, column: int | None, width: int = PREVIEW_CHARS) -> str:
    """The matched line for display: trailing space dropped; a long line is cut to ``width`` characters
    around the first match (``column``, 1-based, from git)."""
    line = text.rstrip()
    if len(line) <= width:
        return line
    start = max(0, min((column or 1) - 1 - _PREVIEW_LEAD, len(line) - width))
    return line[start : start + width]
