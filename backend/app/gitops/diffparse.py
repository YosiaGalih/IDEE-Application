"""``git diff --raw -z -p`` output → the API_CONTRACTS §3 ``Diff`` shape.

The raw section (NUL-separated, exact paths) lists the files. The patch blocks that follow are in the same
order, so block *i* belongs to raw entry *i*. If the counts disagree, parsing fails instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.gitops.errors import GitError

_HUNK = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_STATUS: dict[str, Literal["added", "modified", "deleted"]] = {
    "A": "added",
    "D": "deleted",
    "M": "modified",
    "T": "modified",
}


@dataclass(frozen=True)
class DiffLine:
    type: Literal["context", "add", "del"]
    old_no: int | None
    new_no: int | None
    text: str


@dataclass
class DiffHunk:
    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class DiffFile:
    path: str
    old_path: str | None
    status: Literal["added", "modified", "deleted"]
    additions: int = 0
    deletions: int = 0
    is_binary: bool = False
    hunks: list[DiffHunk] = field(default_factory=list)


@dataclass(frozen=True)
class DiffStats:
    files: int
    additions: int
    deletions: int


@dataclass(frozen=True)
class Diff:
    base: str
    head: str | None  # None: the working tree
    files: list[DiffFile]
    stats: DiffStats
    truncated: bool


def _entries(tokens: list[bytes]) -> tuple[list[tuple[str, str]], int]:
    entries: list[tuple[str, str]] = []
    i = 0
    while i + 1 < len(tokens) and tokens[i].startswith(b":"):
        letter = tokens[i].rsplit(b" ", 1)[-1][:1].decode()
        if letter not in _STATUS:
            raise GitError("diff_parse")  # renames/copies are disabled; anything else is unexpected
        entries.append((letter, tokens[i + 1].decode("utf-8", "surrogateescape")))
        i += 2
    return entries, i


def _blocks(patch: bytes) -> list[list[bytes]]:
    blocks: list[list[bytes]] = []
    for line in patch.split(b"\n"):
        if line.startswith(b"diff --git "):
            blocks.append([])
        elif blocks:
            blocks[-1].append(line)
    return blocks


def parse(
    output: bytes, *, base: str, head: str | None, max_bytes: int, output_truncated: bool = False
) -> Diff:
    """Pair raw entries with patch blocks. A type change (``T``, e.g. file → symlink) has two blocks.

    ``output_truncated``: git's output was cut at the adapter's cap. Files whose blocks were cut off are
    listed without hunks and the diff is marked truncated, instead of failing. Their ``additions`` and
    ``deletions`` then count only what was read; with ``max_bytes`` truncation alone they stay exact.
    """
    tokens = output.split(b"\0")
    entries, i = _entries(tokens)
    if output_truncated and i >= len(tokens) - 1 and entries:
        entries.pop()  # the cap fell inside the raw section: the last path may be cut off mid-name
    patch = b"\0".join(tokens[i + 1 :]) if i < len(tokens) else b""
    blocks = _blocks(patch)
    needed = sum(2 if letter == "T" else 1 for letter, _ in entries)
    if len(blocks) != needed and not (output_truncated and len(blocks) < needed):
        raise GitError("diff_parse")

    budget = max_bytes
    truncated = output_truncated
    files: list[DiffFile] = []
    cursor = 0
    for letter, path in entries:
        status = _STATUS[letter]
        item = DiffFile(path, None if status == "added" else path, status)
        take = 2 if letter == "T" else 1
        # A block boundary resets the hunk: the second block's "---"/"+++" headers are not diff lines.
        lines = [line for block in blocks[cursor : cursor + take] for line in (b"diff --git ", *block)]
        cursor += take
        hunk: DiffHunk | None = None
        old_no = new_no = 0
        for line in lines:
            if line == b"diff --git ":
                hunk = None
                continue
            if line.startswith((b"Binary files ", b"GIT binary patch")):
                item.is_binary = True
                hunk = None
                continue
            if (match := _HUNK.match(line)) is not None:
                old_no, new_no = int(match[1]), int(match[3])
                hunk = DiffHunk(
                    line.decode("utf-8", "replace"),
                    old_no,
                    int(match[2]) if match[2] is not None else 1,
                    new_no,
                    int(match[4]) if match[4] is not None else 1,
                )
                item.hunks.append(hunk)
                continue
            if hunk is None or line[:1] not in (b" ", b"+", b"-"):
                continue  # file headers before the first hunk, "\ No newline", the trailing empty line
            text = line[1:].decode("utf-8", "replace")
            tag = line[:1]
            if tag == b" ":
                entry = DiffLine("context", old_no, new_no, text)
                old_no += 1
                new_no += 1
            elif tag == b"+":
                entry = DiffLine("add", None, new_no, text)
                new_no += 1
                item.additions += 1
            else:
                entry = DiffLine("del", old_no, None, text)
                old_no += 1
                item.deletions += 1
            cost = len(text) + 1
            if truncated or cost > budget:
                truncated = True  # counts stay exact; only the lines shown are cut
                continue
            budget -= cost
            hunk.lines.append(entry)
        files.append(item)
    stats = DiffStats(len(files), sum(f.additions for f in files), sum(f.deletions for f in files))
    return Diff(base, head, files, stats, truncated)
