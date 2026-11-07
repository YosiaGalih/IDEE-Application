"""R0 builtins over the task worktree (TOOL_CATALOG `repo.*`; spec 3.1d2a Goals 3, 4, 6, 7).

Every read goes through ``TaskWorkspace`` (AGENT_READ: denylist, both ``.agentignore`` files, dependency
folders, ``AGENT_READ_LIMIT``). Every output is repository data and goes back wrapped as untrusted.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict
from pathlib import PurePosixPath

from pydantic import Field

from app.core.redaction import Redactor
from app.tools.base import RiskLevel, ToolContext, ToolError, ToolOutput, ToolSpec, Untrusted
from app.tools.builtin._common import StrictInput, relative, workspace_errors, workspace_of
from app.tools.gateway import WINDOW_EDGE_CHARS
from app.tools.policy import TEST_ONLY_PATTERNS, matches_any
from app.workspace.fs import AGENT_READ_LIMIT, LINE_PATTERN, TreeEntry
from app.workspace.policy import Access

TREE_MAX_ENTRIES = 500  # TOOL_CATALOG repo.get_tree
READ_MAX_BYTES = 60 * 1024  # below the 64 KB tool_result, so the gateway never has to cut a file mid-line
RELATED_SCAN_ENTRIES = 5000
RELATED_MAX = 50
RELATED_DEPTH = 10


class GetTreeInput(StrictInput):
    path: str = Field(".", max_length=1000)
    depth: int = Field(3, ge=1, le=10)


class ReadFileInput(StrictInput):
    path: str = Field(min_length=1, max_length=1000)
    start_line: int | None = Field(None, ge=1)
    end_line: int | None = Field(None, ge=1)


class DetectStackInput(StrictInput):
    pass


class FindRelatedTestsInput(StrictInput):
    path: str = Field(min_length=1, max_length=1000)


_BOUNDARY = re.compile(r"[\s\"'`<>()\[\]{},;]")


def _cut_line(line: str, max_bytes: int) -> str:
    """The start of a line longer than one read. Pattern secrets are redacted on the whole line before the
    cut, because a cut secret no longer matches its pattern; the token the cut lands in is dropped whole, as
    the gateway does at its own window edge, because a configured literal is known only to the gateway
    (AG-13 WP-3.1d2a LOW-D4)."""
    head = Redactor().redact(line).encode()[:max_bytes].decode(errors="ignore")
    edge = max(len(head) - WINDOW_EDGE_CHARS, 0)
    end = max((found.end() for found in _BOUNDARY.finditer(head, edge)), default=edge)
    return head[:end]


UNLISTED = " (contents not listed)"  # a folder the walk did not open (depth or entry limit), not an empty one


def _flatten(entries: list[TreeEntry], out: list[str]) -> None:
    for entry in entries:
        if entry.type != "dir":
            out.append(entry.path)
            continue
        out.append(f"{entry.path}/" if entry.children is not None else f"{entry.path}/{UNLISTED}")
        if entry.children:
            _flatten(entry.children, out)


async def get_tree(ctx: ToolContext, raw: GetTreeInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    rel = relative(raw.path)
    with workspace_errors():
        tree = await workspace.tree(rel, depth=raw.depth, max_entries=TREE_MAX_ENTRIES)
    paths: list[str] = []
    _flatten(tree.entries, paths)
    truncated = tree.truncated or len(paths) > TREE_MAX_ENTRIES
    lines = paths[:TREE_MAX_ENTRIES]
    if truncated:
        lines.append(f"[truncated: more than {len(lines)} entries; list a sub-folder or lower depth]")
    return ToolOutput("\n".join(lines) or "[empty]", untrusted=Untrusted("repo.get_tree", rel))


async def read_file(ctx: ToolContext, raw: ReadFileInput) -> ToolOutput:
    """Numbered lines, where only ``\\n`` ends a line (as in the editor and git). The whole file is read (at
    most AGENT_READ_LIMIT) so a range past the end is said, not shown as empty."""
    workspace = workspace_of(ctx)
    rel = relative(raw.path)
    if raw.end_line is not None and raw.end_line < (raw.start_line or 1):
        raise ToolError("invalid_range", "end_line is before start_line")
    with workspace_errors():
        got = await workspace.read_text(rel)
    if got.too_large:
        raise ToolError("file_too_large", f"the file is over {AGENT_READ_LIMIT} bytes; agents never read it")
    if got.content is None or got.encoding != "utf-8":
        raise ToolError("binary_file", "the file is not text")
    lines = LINE_PATTERN.findall(got.content)
    first = raw.start_line or 1
    if lines and first > len(lines):
        message = f"[no lines at or after line {first}; the file has {len(lines)} lines]"
        return ToolOutput(message, untrusted=Untrusted("repo.read_file", got.path))
    last = len(lines) if raw.end_line is None else min(raw.end_line, len(lines))
    out: list[str] = []
    used = 0
    for number in range(first, last + 1):
        line = lines[number - 1].removesuffix("\n")
        numbered = f"{number}\t{line}\n"
        size = len(numbered.encode())
        if used + size > READ_MAX_BYTES:
            resume = number
            if not out:  # one line longer than a whole read: show its start, say so
                head = _cut_line(numbered, READ_MAX_BYTES - 200)
                out.append(f"{head}\n[line {number} is longer than one read; the rest of it is not shown]\n")
                resume = number + 1
            if resume <= last:
                out.append(f"[truncated at line {resume - 1}; read on with start_line={resume}]\n")
            break
        out.append(numbered)
        used += size
    if raw.end_line is not None and raw.end_line > len(lines) and out:
        out.append(f"[the file ends at line {len(lines)}]\n")
    return ToolOutput("".join(out) or "[empty file]", untrusted=Untrusted("repo.read_file", got.path))


async def detect_stack(ctx: ToolContext, raw: DetectStackInput) -> ToolOutput:
    workspace = workspace_of(ctx)
    with workspace_errors():
        stack = await workspace.detect_stack()
    return ToolOutput(asdict(stack), untrusted=Untrusted("repo.detect_stack"))


_TOKEN = re.compile(r"[^a-z0-9]+")


def _related(name: str, stem: str) -> bool:
    lowered = name.lower()
    return (
        stem in _TOKEN.split(lowered)
        or lowered.startswith((f"test{stem}", f"{stem}test"))
        or lowered.split(".", 1)[0] in (f"{stem}test", f"{stem}tests", f"test{stem}")
    )


def _related_in(paths: list[str], rel: str, stem: str) -> list[str]:
    return sorted(
        p
        for p in paths
        if not p.endswith("/")
        and not p.endswith(UNLISTED)
        and p != rel
        and matches_any(p, TEST_ONLY_PATTERNS)
        and _related(PurePosixPath(p).name, stem)
    )


async def find_related_tests(ctx: ToolContext, raw: FindRelatedTestsInput) -> ToolOutput:
    """Test files (TOOL_CATALOG test patterns) whose name carries the stem of ``path``: deterministic, no
    content read. ``foo.test.ts`` → stem ``foo``. Folders the walk did not open are said."""
    workspace = workspace_of(ctx)
    rel = relative(raw.path)
    stem = PurePosixPath(rel).name.split(".", 1)[0].lower()
    if not stem or stem in ("test", "tests", "spec"):
        raise ToolError("invalid_path", "name a source file, not a test folder")
    with workspace_errors():
        if (await workspace.policy()).denied(PurePosixPath(rel).parts, Access.AGENT_READ):
            raise ToolError("path_denied", "agents may not read or write this path")
        tree = await workspace.tree(".", depth=RELATED_DEPTH, max_entries=RELATED_SCAN_ENTRIES)
    paths: list[str] = []
    _flatten(tree.entries, paths)
    found = await asyncio.to_thread(_related_in, paths, rel, stem)
    lines = found[:RELATED_MAX]
    if len(found) > RELATED_MAX:
        lines.append(f"[{len(found) - RELATED_MAX} more found; narrow with repo.search_text]")
    if tree.truncated:
        lines.append("[the worktree is larger than one scan: some folders were not searched]")
    elif any(p.endswith(UNLISTED) for p in paths):
        lines.append(f"[some folders were not searched: deeper than {RELATED_DEPTH} levels, or symlinks]")
    return ToolOutput("\n".join(lines) or "[none found]", untrusted=Untrusted("repo.find_related_tests", rel))


def specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            "repo.get_tree",
            "List files and folders of the task worktree (paths, folders end with /).",
            GetTreeInput,
            RiskLevel.R0,
            get_tree,
            timeout_s=30,
        ),
        ToolSpec(
            "repo.read_file",
            "Read a text file of the task worktree with line numbers; start_line/end_line for large files.",
            ReadFileInput,
            RiskLevel.R0,
            read_file,
            timeout_s=30,
        ),
        ToolSpec(
            "repo.detect_stack",
            "Detect languages, frameworks, package managers, test profiles and entry points.",
            DetectStackInput,
            RiskLevel.R0,
            detect_stack,
            timeout_s=30,
        ),
        ToolSpec(
            "repo.find_related_tests",
            "Find test files whose name matches a source file (by name only).",
            FindRelatedTestsInput,
            RiskLevel.R0,
            find_related_tests,
            timeout_s=30,
        ),
    ]
