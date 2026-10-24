"""Unified diff parsing and hunk application (FR-031, SECURITY_MODEL T9). Pure text; no I/O.

**Supported**
- The git/unified subset an agent or the IDE produces:
  - ``diff --git`` headers (optional), ``---``/``+++`` with ``/dev/null`` for add/delete;
  - ``@@`` hunks, ``\\ No newline at end of file``;
  - empty-file add/delete written as a bare ``diff --git`` block.
- Prose between file blocks is skipped, as ``git apply`` does.

**Rejected** (``PatchRejected``)
- Rename, copy, mode change, binary patch, quoted paths, a path twice.
- More than 1 MiB of diff or more than 200 files.

**Placement**
- A hunk applies where its header says or at the single other place its context matches (an offset).
- There is **no fuzz**: context must match exactly, ignoring only ``\\r\\n`` vs ``\\n``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.workspace.errors import PatchRejected

MAX_DIFF_BYTES = 1024 * 1024  # [Menebak]
MAX_FILES = 200  # [Menebak]
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_HEADER_REJECTS = (
    (("rename from", "rename to", "similarity index", "dissimilarity index"), "rename"),
    (("copy from", "copy to"), "copy"),
    (("old mode", "new mode"), "mode"),
    (("Binary files", "GIT binary patch"), "binary"),
)
_HEADER_OK = ("index ", "new file mode", "deleted file mode")


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]]  # (" " | "-" | "+", text without EOL)
    new_no_eol: bool


@dataclass(frozen=True)
class FilePatch:
    old_path: str | None  # None: the file is added
    new_path: str | None  # None: the file is deleted
    hunks: list[Hunk]

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


class HunkConflict(Exception):
    def __init__(self, hunk_index: int, reason: str) -> None:
        super().__init__(f"hunk {hunk_index}: {reason}")
        self.hunk_index = hunk_index
        self.reason = reason


def _side_path(raw: str) -> str | None:
    raw = raw.split("\t", 1)[0]
    if raw.startswith('"'):
        raise PatchRejected("quoted_path")
    if raw == "/dev/null":
        return None
    return raw[2:] if raw.startswith(("a/", "b/")) else raw


def _git_header_path(rest: str) -> str:
    """``a/P b/P`` → ``P``; only identical sides are accepted (renames are rejected earlier)."""
    size = (len(rest) - 5) // 2
    path = rest[2 : 2 + size]
    if size <= 0 or rest != f"a/{path} b/{path}":
        raise PatchRejected("malformed")
    return path


def _parse_hunk(lines: list[str], i: int) -> tuple[Hunk, int]:
    match = _HUNK_HEADER.match(lines[i])
    if match is None:
        raise PatchRejected("malformed")
    old_start, new_start = int(match[1]), int(match[3])
    old_count = int(match[2]) if match[2] is not None else 1
    new_count = int(match[4]) if match[4] is not None else 1
    i += 1
    body: list[tuple[str, str]] = []
    old_seen = new_seen = 0
    new_no_eol = False
    while old_seen < old_count or new_seen < new_count or (i < len(lines) and lines[i].startswith("\\")):
        if i >= len(lines):
            raise PatchRejected("malformed")
        line = lines[i]
        i += 1
        if line.startswith("\\"):  # "\ No newline at end of file" qualifies the line before it
            if not body:
                raise PatchRejected("malformed")
            if body[-1][0] in " +":
                new_no_eol = True
            continue
        tag, text = (" ", "") if line == "" else (line[0], line[1:])  # LLMs drop the space of blank context
        if tag not in " -+":
            raise PatchRejected("malformed")
        old_seen += tag in " -"
        new_seen += tag in " +"
        if old_seen > old_count or new_seen > new_count:
            raise PatchRejected("malformed")
        body.append((tag, text))
    return Hunk(old_start, old_count, new_start, new_count, body, new_no_eol), i


def parse_unified_diff(text: str) -> list[FilePatch]:
    if len(text.encode("utf-8")) > MAX_DIFF_BYTES:
        raise PatchRejected("too_large")
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    patches: list[FilePatch] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            git_path = line[len("diff --git ") :]
            i += 1
            created = deleted = False
            while i < len(lines) and not lines[i].startswith(("--- ", "diff --git ")):
                header = lines[i]
                for prefixes, reason in _HEADER_REJECTS:
                    if header.startswith(prefixes):
                        raise PatchRejected(reason)
                if not header.startswith(_HEADER_OK):
                    raise PatchRejected("malformed")
                created = created or header.startswith("new file mode")
                deleted = deleted or header.startswith("deleted file mode")
                i += 1
            if i < len(lines) and lines[i].startswith("--- "):
                continue  # the ---/+++ pair names the paths
            if created == deleted:  # neither, or both: nothing this apply path can do
                raise PatchRejected("malformed")
            path = _git_header_path(git_path)
            patches.append(FilePatch(None, path, []) if created else FilePatch(path, None, []))
        elif line.startswith("--- "):
            if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
                raise PatchRejected("malformed")
            old_path, new_path = _side_path(line[4:]), _side_path(lines[i + 1][4:])
            if old_path is None and new_path is None:
                raise PatchRejected("malformed")
            if old_path is not None and new_path is not None and old_path != new_path:
                raise PatchRejected("rename")
            i += 2
            hunks: list[Hunk] = []
            while i < len(lines) and lines[i].startswith("@@"):
                hunk, i = _parse_hunk(lines, i)
                hunks.append(hunk)
            if not hunks:
                raise PatchRejected("malformed")
            patches.append(FilePatch(old_path, new_path, hunks))
        elif line.startswith(("Binary files ", "GIT binary patch")):
            raise PatchRejected("binary")
        else:
            i += 1
        if len(patches) > MAX_FILES:
            raise PatchRejected("too_many_files")
    if not patches:
        raise PatchRejected("empty")
    paths = [p.path for p in patches]
    if len(set(paths)) != len(paths):
        raise PatchRejected("duplicate_path")
    return patches


def _split(text: str) -> tuple[list[str], bool, str]:
    """Lines without EOL, whether the text ends with a newline, and its dominant EOL."""
    crlf = text.count("\r\n")
    eol = "\r\n" if crlf > text.count("\n") - crlf else "\n"
    if not text:
        return [], True, eol
    body = text[:-1] if text.endswith("\n") else text
    return [line.removesuffix("\r") for line in body.split("\n")], text.endswith("\n"), eol


def apply_hunks(old: str | None, hunks: list[Hunk]) -> str:
    """The new text, or ``HunkConflict`` for the first hunk that does not apply."""
    old_lines, trailing, eol = _split(old or "")
    out: list[str] = []
    cursor = 0  # index in old_lines up to which the output is final
    delta = 0  # offset found for the previous hunk, carried to the next (as ``patch`` does)
    for index, hunk in enumerate(hunks):
        expected = [text for tag, text in hunk.lines if tag in " -"]
        replacement = [text for tag, text in hunk.lines if tag in " +"]
        header_pos = hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1
        preferred = header_pos + delta
        size = len(expected)

        def matches(at: int, expected: list[str] = expected, size: int = size) -> bool:
            return 0 <= at <= len(old_lines) - size and old_lines[at : at + size] == expected

        if preferred >= cursor and matches(preferred):
            pos = preferred
        elif not expected:
            raise HunkConflict(index, "context_mismatch")  # a pure insertion has no context to search for
        else:
            found = [at for at in range(cursor, len(old_lines) - size + 1) if matches(at)]
            if len(found) > 1:
                raise HunkConflict(index, "ambiguous")
            if not found:
                earlier = any(matches(at) for at in range(cursor))
                raise HunkConflict(index, "overlap" if earlier else "context_mismatch")
            pos = found[0]
        out += old_lines[cursor:pos]
        out += replacement
        cursor = pos + size
        delta = pos - header_pos
        if cursor == len(old_lines):
            trailing = not hunk.new_no_eol
    out += old_lines[cursor:]
    if not out:
        return ""
    return eol.join(out) + (eol if trailing else "")
