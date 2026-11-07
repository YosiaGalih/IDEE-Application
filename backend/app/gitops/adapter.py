"""GitAdapter (ARCHITECTURE §4.2, §7; ADR-016; SECURITY_MODEL T3, T7): the only place the backend starts a
process (``tests/security/test_no_host_exec.py``).

**Why git is dangerous here.** Repository content is untrusted. Sandboxed code writes the task worktree, and
an imported project can carry anything. git executes commands named in its configuration:
- hooks;
- ``core.fsmonitor``;
- filter and diff drivers;
- pagers, editors, credential helpers.

So every call:
- passes **explicit** ``--git-dir`` and ``--work-tree``. git never *discovers* a repository from the work
  tree, so a ``<worktree>/.git`` rewritten by sandboxed code is never read. The git dir of a worktree comes
  from the trusted registry in ``<repo>/.git/worktrees/*/gitdir``.
- runs with an environment built from scratch: no system/global config, no prompts, literal pathspecs.
- overrides the dangerous settings with ``-c`` (hooks, fsmonitor, pager, credential helper, protocols).
- has a timeout that kills the whole process group, and a stdout cap.

**What is not here.** No ``push``, ``fetch``, ``clone``, ``reset`` or ``clean``, and no ``--force`` except
``worktree remove``. Remote operations are R3/P2 and arrive with their own WP.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
import stat
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.core.logging import get_logger
from app.gitops import diffparse
from app.gitops.diffparse import Diff
from app.gitops.errors import GitError, GitInputError

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
TASK_BRANCH_PREFIX = "kk/task-"
WORKSPACE_TEMP_PATTERN = ".kk-tmp-*"  # app.workspace.fs.TEMP_PREFIX; gitops must not import workspace
SANDBOX_ARTIFACTS_PATTERN = "/.kk/"  # sandbox artefacts never enter git (SECURITY_MODEL §4)
_EXCLUDE_PATTERNS = (WORKSPACE_TEMP_PATTERN, SANDBOX_ARTIFACTS_PATTERN)
STDERR_KEEP = 2048
_CHUNK = 64 * 1024
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_SHA = re.compile(r"[0-9a-f]{7,40}")
_AUTHOR_FORBIDDEN = re.compile(r"[<>\n\r\x00]")
_CONFIG = (
    "core.hooksPath=/dev/null",
    "protocol.allow=never",
    "protocol.https.allow=always",
    "core.fsmonitor=false",
    "core.pager=cat",
    "core.quotePath=false",
    "credential.helper=",
    "submodule.recurse=false",
    "commit.gpgSign=false",
    "tag.gpgSign=false",
)
_BASE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_ASKPASS": "/bin/false",
    "GIT_EDITOR": "/bin/false",
    "GIT_LITERAL_PATHSPECS": "1",
}
# Repository config keys git itself writes for a local repo, a worktree, or a clone. Anything else (hooks,
# fsmonitor, filter/diff drivers, include, alias, extensions) is refused, not just overridden (ADR-026 (4)).
_ALLOWED_CONFIG = re.compile(
    r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks)"
    r"|remote\..+\.(url|fetch)"
    r"|branch\..+\.(remote|merge)"
)
_ALTERNATES = ("alternates", "http-alternates")
_DIFF_FLAGS = ("diff", "--raw", "-z", "-p", "--no-renames", "--no-ext-diff", "--no-textconv", "--no-color")

logger = get_logger(__name__)


@dataclass(frozen=True)
class GitTarget:
    repo: Path  # <project>/repo: the main working tree, holding the trusted .git directory
    worktree: Path | None = None  # <project>/tasks/<id>: a registered task worktree


@dataclass(frozen=True)
class Author:
    name: str
    email: str


@dataclass(frozen=True)
class Change:
    path: str
    status: Literal["added", "modified", "deleted", "untracked", "conflicted"]


@dataclass(frozen=True)
class NameList:
    paths: list[str]  # sorted, unique
    skipped: int  # names that are not UTF-8: not addressable through the API, reported, never hidden silently


def _names(raw: bytes) -> NameList:
    names: set[str] = set()
    skipped = 0
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            names.add(item.decode("utf-8"))
        except UnicodeDecodeError:
            skipped += 1
    return NameList(sorted(names), skipped)


@dataclass(frozen=True)
class GitStatus:
    branch: str | None
    head: str | None
    clean: bool
    changes: list[Change]


@dataclass(frozen=True)
class LogEntry:
    commit: str
    author: str
    date: str
    message: str


@dataclass(frozen=True)
class Branch:
    name: str
    head: str
    is_task_branch: bool


@dataclass(frozen=True)
class MergeResult:
    merged: bool
    commit: str | None
    conflicts: list[str]
    reason: Literal["merged", "up_to_date", "conflict", "dirty"]


@dataclass(frozen=True)
class _Result:
    returncode: int
    stdout: bytes
    stderr: bytes
    truncated: bool = False  # stdout was cut at ``max_output`` (only when the caller allowed it)


@dataclass(frozen=True)
class TreeFile:
    """A regular file of a commit's tree (``ls-tree``)."""

    path: str
    size: int


@dataclass(frozen=True)
class TreeListing:
    files: list[TreeFile]
    truncated: bool  # the listing was cut at ``max_output``
    skipped_paths: int  # regular files whose names are not UTF-8 (not addressable through the API)


@dataclass(frozen=True)
class GrepMatch:
    path: str
    line: int
    column: int | None  # 1-based byte column of the first match; working tree only
    text: str  # the line, or only the match with ``only_matching``


@dataclass(frozen=True)
class GrepResult:
    matches: list[GrepMatch]
    truncated: bool  # output cut at ``max_output``: more matches exist
    skipped_paths: int  # matches in files whose names are not UTF-8 (not addressable through the API)


class _TooLarge(Exception):
    pass


# --- input validation ---------------------------------------------------------------------------------------


def _branch(name: str) -> str:
    if (
        not _BRANCH.fullmatch(name)
        or ".." in name
        or "//" in name
        or "/." in name
        or name.endswith((".lock", "/", "."))
    ):
        raise GitInputError("branch")
    return name


def _ref(ref: str) -> str:
    if _SHA.fullmatch(ref):
        return ref
    try:
        return _branch(ref)
    except GitInputError:
        raise GitInputError("ref") from None


_MESSAGE_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # log() separates fields with \x1e/\x1f


def _message(message: str) -> str:
    if not 1 <= len(message) <= 5000 or not message.strip() or _MESSAGE_FORBIDDEN.search(message):
        raise GitInputError("message")
    return message


def _author(author: Author) -> Author:
    if (
        not 1 <= len(author.name) <= 200
        or not 3 <= len(author.email) <= 254
        or _AUTHOR_FORBIDDEN.search(author.name + author.email)
    ):
        raise GitInputError("author")
    return author


def _pathspec(path: str) -> str:
    if not path or "\x00" in path or path.startswith("/") or ".." in path.split("/"):
        raise GitInputError("path")
    return path


def below_a_non_folder(tree: Path, names: list[str]) -> set[str]:
    """The names with a parent that is not a real folder in ``tree`` (a symlink, a file, or missing). git
    takes such a path as deleted, and staging it as a file fails ("beyond a symbolic link"). Parents are read
    with ``lstat``, never followed (AG-13 WP-3.1d2a LOW-D1/LOW-D3). File system work: call it in a thread."""
    folders: dict[str, bool] = {}
    gone: set[str] = set()
    for name in names:
        parts = name.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            parent = "/".join(parts[:depth])
            real = folders.get(parent)
            if real is None:
                try:
                    real = stat.S_ISDIR(os.lstat(tree / parent).st_mode)
                except OSError:
                    real = False
                folders[parent] = real
            if not real:
                gone.add(name)
                break
    return gone


def _identity_env(author: Author, committer: Author) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": author.name,
        "GIT_AUTHOR_EMAIL": author.email,
        "GIT_COMMITTER_NAME": committer.name,
        "GIT_COMMITTER_EMAIL": committer.email,
    }


# WP-2.1d D1: git grep runs a user's or agent's regex inside git; a hard address-space limit (prlimit, from
# util-linux in the backend image) keeps a pathological pattern from taking the host's memory.
# [Menebak] 512 MiB.
GREP_MEMORY_LIMIT = 512 * 1024 * 1024


class GitAdapter:
    def __init__(
        self,
        git: str = "git",
        *,
        prlimit: str = "prlimit",
        timeout_s: float = 60.0,  # [Menebak]
        max_output: int = 16 * 1024 * 1024,  # [Menebak]
        committer: Author = Author("KodeKantor", "kodekantor@localhost"),  # noqa: B008 - frozen value
    ) -> None:
        self.git = git
        self.prlimit = prlimit
        self.timeout_s = timeout_s
        self.max_output = max_output
        self.committer = _author(committer)

    # --- process -----------------------------------------------------------------------------------------

    async def _run(
        self,
        args: list[str],
        *,
        git_dir: Path | None,
        work_tree: Path | None,
        cwd: Path,
        env: dict[str, str] | None = None,
        check: bool = True,
        truncate: bool = False,
        timeout_s: float | None = None,
        max_output: int | None = None,
        memory_limit: int | None = None,
    ) -> _Result:
        """Run git. ``truncate``: past ``max_output`` stop reading and return what was read (no error).
        ``timeout_s`` and ``max_output`` override the adapter's limits for this call. ``memory_limit``: git
        runs under ``prlimit --as`` (bytes); a git that cannot start under it fails like any git error."""
        argv = [self.git]
        if memory_limit is not None:
            argv = [self.prlimit, f"--as={memory_limit}", "--", self.git]
        for setting in _CONFIG:
            argv += ["-c", setting]
        if git_dir is not None:
            argv.append(f"--git-dir={git_dir}")
        if work_tree is not None:
            argv.append(f"--work-tree={work_tree}")
        argv += args
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env={**_BASE_ENV, **(env or {})},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # its own process group, so a timeout kills every child too
            )
        except FileNotFoundError:
            if memory_limit is not None:  # the image lacks util-linux: fail visibly, never run unbounded
                raise GitError("prlimit_missing") from None
            raise
        try:
            stdout, stderr, cut = await asyncio.wait_for(
                self._collect(proc, truncate, self.max_output if max_output is None else max_output),
                self.timeout_s if timeout_s is None else timeout_s,
            )
        except TimeoutError:
            await _kill(proc)
            raise GitError("timeout") from None
        except _TooLarge:
            await _kill(proc)
            raise GitError("output_too_large") from None
        except BaseException:
            await _kill(proc)
            raise
        result = _Result(proc.returncode if proc.returncode is not None else -1, stdout, stderr, cut)
        if check and result.returncode != 0 and not cut:
            self._log_failure(args[0], result, work_tree or cwd)
            raise GitError(f"{args[0]}_failed")
        return result

    async def _collect(
        self, proc: asyncio.subprocess.Process, truncate: bool, limit: int
    ) -> tuple[bytes, bytes, bool]:
        assert proc.stdout is not None and proc.stderr is not None
        stdout_stream, stderr_stream = proc.stdout, proc.stderr

        async def out() -> tuple[bytes, bool]:
            chunks: list[bytes] = []
            size = 0
            while chunk := await stdout_stream.read(_CHUNK):
                if size + len(chunk) > limit:
                    if not truncate:
                        raise _TooLarge()
                    chunks.append(chunk[: limit - size])
                    return b"".join(chunks), True
                size += len(chunk)
                chunks.append(chunk)
            return b"".join(chunks), False

        async def err() -> bytes:
            kept = bytearray()
            while chunk := await stderr_stream.read(_CHUNK):
                kept += chunk[: max(0, STDERR_KEEP - len(kept))]
            return bytes(kept)

        err_task = asyncio.ensure_future(err())
        try:
            stdout, cut = await out()
            if cut:
                await _kill(proc)  # stop git; the rest of its output is not wanted
            stderr = await err_task
        finally:
            err_task.cancel()
        await proc.wait()
        return stdout, stderr, cut

    def _log_failure(self, command: str, result: _Result, tree: Path) -> None:
        text = result.stderr.decode("utf-8", "replace").replace(str(tree.parent), "<workspace>")
        logger.warning("git_failed", command=command, returncode=result.returncode, stderr=text)

    # --- targets -----------------------------------------------------------------------------------------

    async def _adirs(self, target: GitTarget, *, verify_gitfile: bool = True) -> tuple[Path, Path]:
        """The trusted git dir and work tree for ``target``, after checking the repository (ADR-026 (4)).

        Refused with ``git_metadata_unsafe``:
        - a repository config key outside ``_ALLOWED_CONFIG`` (hooks, fsmonitor, filters, includes, aliases);
        - object alternates;
        - a worktree whose ``.git`` file does not point at its registered git dir (``verify_gitfile``).
        """
        git_dir, tree = await asyncio.to_thread(self._dirs, target, verify_gitfile)
        await self._verify_repository(target.repo / ".git")
        return git_dir, tree

    async def _verify_repository(self, common: Path) -> None:
        unsafe = await asyncio.to_thread(
            lambda: any(os.path.lexists(common / "objects" / "info" / name) for name in _ALTERNATES)
        )
        if unsafe:
            raise GitError("git_metadata_unsafe")
        listed = await self._run(
            ["config", "--file", str(common / "config"), "--no-includes", "--list", "--name-only", "-z"],
            git_dir=common,
            work_tree=None,
            cwd=common,
        )
        for key in listed.stdout.decode("utf-8", "surrogateescape").split("\0"):
            if key and not _ALLOWED_CONFIG.fullmatch(key.strip()):
                logger.warning("git_config_key_refused", key=key.strip()[:120])
                raise GitError("git_metadata_unsafe")

    def _dirs(self, target: GitTarget, verify_gitfile: bool = True) -> tuple[Path, Path]:
        git_dir = target.repo / ".git"
        if git_dir.is_symlink() or not git_dir.is_dir():
            raise GitError("not_a_repo")
        if target.worktree is None:
            return git_dir, target.repo
        return self._worktree_gitdir(target.repo, target.worktree, verify_gitfile), target.worktree

    @staticmethod
    def _worktree_gitdir(repo: Path, worktree: Path, verify_gitfile: bool = True) -> Path:
        """The worktree's git dir from the repo's registry; ``<worktree>/.git`` must point back at it."""
        expected = os.path.join(os.path.realpath(worktree), ".git")
        registry = repo / ".git" / "worktrees"
        with contextlib.suppress(FileNotFoundError):
            for name in sorted(os.listdir(registry)):
                try:
                    recorded = (registry / name / "gitdir").read_text().strip()
                except OSError:
                    continue
                normalised = os.path.join(os.path.realpath(os.path.dirname(recorded)), ".git")
                if normalised == expected:
                    if verify_gitfile and not _gitfile_points_at(expected, registry / name):
                        raise GitError("git_metadata_unsafe")  # rewritten by code that ran in the worktree
                    return registry / name
        raise GitError("not_a_worktree")

    # --- read ----------------------------------------------------------------------------------------------

    async def init(self, repo: Path, default_branch: str) -> None:
        _branch(default_branch)
        if not await asyncio.to_thread(repo.is_dir):
            raise GitError("not_found")
        if await asyncio.to_thread(os.path.lexists, repo / ".git"):
            raise GitError("already_a_repo")
        await self._run(
            ["init", "-q", "-b", default_branch, str(repo)], git_dir=None, work_tree=None, cwd=repo
        )
        await asyncio.to_thread(_ensure_excludes, repo / ".git")

    async def current_commit(self, target: GitTarget) -> str | None:
        git_dir, tree = await self._adirs(target)
        result = await self._run(
            ["rev-parse", "--verify", "-q", "HEAD^{commit}"],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
            check=False,
        )
        return result.stdout.decode().strip() if result.returncode == 0 else None

    async def head_commit(self, target: GitTarget) -> str | None:
        """HEAD's commit, strictly: ``None`` only for a branch with no commit yet (unborn). A HEAD git cannot
        resolve (a ref to a missing object, a broken repository) is ``GitError("head_unreadable")``, never
        "no commit": callers that act on "no commit" (the index job empties the index) must not see it."""
        sha = await self.current_commit(target)
        if sha is not None:
            return sha
        git_dir, tree = await self._adirs(target)
        ref = await self._run(
            ["symbolic-ref", "-q", "HEAD"], git_dir=git_dir, work_tree=tree, cwd=tree, check=False
        )
        name = ref.stdout.decode("utf-8", "replace").strip()
        if ref.returncode != 0 or not name.startswith("refs/heads/"):
            raise GitError("head_unreadable")
        # --exists: 0 the ref exists (yet HEAD did not resolve: broken), 2 it does not (unborn), else error.
        exists = await self._run(
            ["show-ref", "--exists", name], git_dir=git_dir, work_tree=tree, cwd=tree, check=False
        )
        if exists.returncode != 2:
            raise GitError("head_unreadable")
        return None

    async def status(self, target: GitTarget) -> GitStatus:
        git_dir, tree = await self._adirs(target)
        result = await self._run(
            ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all", "--no-renames"],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
        )
        branch: str | None = None
        head: str | None = None
        changes: list[Change] = []
        for token in result.stdout.split(b"\0"):
            text = token.decode("utf-8", "surrogateescape")
            if text.startswith("# branch.oid "):
                oid = text.removeprefix("# branch.oid ")
                head = None if oid == "(initial)" else oid
            elif text.startswith("# branch.head "):
                name = text.removeprefix("# branch.head ")
                branch = None if name == "(detached)" else name
            elif text.startswith("1 "):
                fields = text.split(" ", 8)
                xy = fields[1]
                kind: Literal["added", "modified", "deleted"] = (
                    "added" if xy[0] == "A" else "deleted" if "D" in xy else "modified"
                )
                changes.append(Change(fields[8], kind))
            elif text.startswith("u "):
                changes.append(Change(text.split(" ", 10)[10], "conflicted"))
            elif text.startswith("? "):
                changes.append(Change(text[2:], "untracked"))
        return GitStatus(branch, head, not changes, changes)

    async def log(self, target: GitTarget, limit: int = 10) -> list[LogEntry]:
        if await self.current_commit(target) is None:
            return []
        git_dir, tree = await self._adirs(target)
        result = await self._run(
            [
                "log",
                f"-n{min(max(limit, 1), 200)}",
                "--no-color",
                "--format=%H%x1f%an <%ae>%x1f%aI%x1f%s%x1e",
            ],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
        )
        entries: list[LogEntry] = []
        for record in result.stdout.decode("utf-8", "replace").split("\x1e"):
            fields = record.strip("\n").split("\x1f")
            if len(fields) == 4:
                entries.append(LogEntry(*fields))
        return entries

    async def branches(self, target: GitTarget) -> list[Branch]:
        git_dir, tree = await self._adirs(target)
        result = await self._run(
            ["for-each-ref", "--format=%(refname:short)%00%(objectname)", "refs/heads"],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
        )
        found: list[Branch] = []
        for line in result.stdout.decode("utf-8", "surrogateescape").split("\n"):
            if not line:
                continue
            name, _, head = line.partition("\0")
            found.append(Branch(name, head, name.startswith(TASK_BRANCH_PREFIX)))
        return found

    async def _resolve(self, git_dir: Path, tree: Path, ref: str) -> str:
        result = await self._run(
            ["rev-parse", "--verify", "-q", f"{_ref(ref)}^{{commit}}"],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
            check=False,
        )
        if result.returncode != 0:
            raise GitError("unknown_ref")
        return result.stdout.decode().strip()

    async def diff(
        self,
        target: GitTarget,
        *,
        base: str | None = None,
        head: str | None = None,
        paths: list[str] | None = None,
        max_bytes: int = 1024 * 1024,
        exact_files: bool = False,
    ) -> Diff:
        """``base``..``head``; ``head=None`` is the working tree including untracked files. ``exact_files``
        (agent tools): ``paths`` are exact file names, and only they are staged for the working-tree diff;
        otherwise the whole working tree is staged and ``paths`` are pathspecs (folders allowed)."""
        git_dir, tree = await self._adirs(target)
        if base is not None:
            base_sha = await self._resolve(git_dir, tree, base)
        else:
            base_sha = await self.current_commit(target) or EMPTY_TREE
        pathspec = ["--", *(_pathspec(p) for p in paths)] if paths else []
        if head is not None:
            head_sha = await self._resolve(git_dir, tree, head)
            result = await self._run(
                [*_DIFF_FLAGS, base_sha, head_sha, *pathspec],
                git_dir=git_dir,
                work_tree=tree,
                cwd=tree,
                truncate=True,
            )
            return diffparse.parse(
                result.stdout,
                base=base_sha,
                head=head_sha,
                max_bytes=max_bytes,
                output_truncated=result.truncated,
            )
        files = list(paths or []) if exact_files else None
        async with self._worktree_index(target, git_dir, tree, files) as env:
            result = await self._run(
                [*_DIFF_FLAGS, "--cached", base_sha, *pathspec],
                git_dir=git_dir,
                work_tree=tree,
                cwd=tree,
                env=env,
                truncate=True,
            )
        return diffparse.parse(
            result.stdout,
            base=base_sha,
            head=None,
            max_bytes=max_bytes,
            output_truncated=result.truncated,
        )

    @contextlib.asynccontextmanager
    async def _worktree_index(
        self, target: GitTarget, git_dir: Path, tree: Path, files: list[str] | None = None
    ) -> AsyncIterator[dict[str, str]]:
        """A copy of the index and a scratch object store with the working tree staged (untracked files too):
        everything, or only ``files`` (exact paths: added, updated, or removed when gone, a file replacing
        a folder included). The blobs that creates land in the scratch store, so reading a diff never adds
        objects to the repository (git may still freshen the mtime of objects it already has). Yields the
        environment that makes git use them."""
        with tempfile.TemporaryDirectory(prefix="kk-git-diff-") as scratch:
            index = Path(scratch) / "index"
            objects = Path(scratch) / "objects"
            await asyncio.to_thread(objects.mkdir)
            with contextlib.suppress(FileNotFoundError):
                # copy2 keeps the index's mtime: git tells a racily clean entry (written in the index's
                # own second) by it; with a fresh mtime it would skip hashing a same-size edit.
                await asyncio.to_thread(shutil.copy2, git_dir / "index", index)
            env = {
                "GIT_INDEX_FILE": str(index),
                "GIT_OBJECT_DIRECTORY": str(objects),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(target.repo / ".git" / "objects"),
            }
            if files is None:
                await self._run(["add", "-A"], git_dir=git_dir, work_tree=tree, cwd=tree, env=env)
            else:
                await self._stage_exact(git_dir, tree, files, env)
            yield env

    async def _stage_exact(self, git_dir: Path, tree: Path, files: list[str], env: dict[str, str]) -> None:
        """Make each index entry of ``files`` what the working tree holds at that exact path: added, updated,
        removed when gone, a file replacing a folder included; a path below a symlink or a file is gone."""
        checked = [_pathspec(name) for name in files]
        gone = await asyncio.to_thread(below_a_non_folder, tree, checked)
        removed = [name for name in checked if name in gone]
        present = [name for name in checked if name not in gone]
        if removed:
            await self._run(
                ["update-index", "--force-remove", "--", *removed],
                git_dir=git_dir,
                work_tree=tree,
                cwd=tree,
                env=env,
            )
        if present:
            await self._run(
                ["update-index", "--add", "--remove", "--replace", "--", *present],
                git_dir=git_dir,
                work_tree=tree,
                cwd=tree,
                env=env,
            )

    async def changed_paths(self, target: GitTarget, *, base: str) -> NameList:
        """The paths whose content differs between ``base`` and the working tree, untracked (not ignored)
        files included, sorted; names only and without staging anything, so a caller can filter them before
        any content is read (WP-3.1d2a git.diff). A deletion is listed under the deleted name. Names that
        are not UTF-8 are counted in ``skipped``, never dropped silently."""
        git_dir, tree = await self._adirs(target)
        base_sha = await self._resolve(git_dir, tree, base)
        tracked = await self._run(
            ["diff", "--name-only", "--no-renames", "-z", base_sha], git_dir=git_dir, work_tree=tree, cwd=tree
        )
        untracked = await self._run(
            ["ls-files", "-z", "--others", "--exclude-standard"], git_dir=git_dir, work_tree=tree, cwd=tree
        )
        return _names(tracked.stdout + b"\0" + untracked.stdout)

    async def worktree_files(self, target: GitTarget) -> NameList:
        """What a commit of the working tree could take: tracked files plus untracked ones that are not
        ignored (``.gitignore``, ``info/exclude``), sorted, names only. Names that are not UTF-8 are counted
        in ``skipped``."""
        git_dir, tree = await self._adirs(target)
        result = await self._run(
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
        )
        return _names(result.stdout)

    async def list_files(self, target: GitTarget, commit: str) -> TreeListing:
        """The regular files of ``commit``'s tree, read from the object database (no working tree, no symlink
        followed). Symlinks and submodules are left out; paths that are not UTF-8 are counted, not listed."""
        if not _SHA.fullmatch(commit):
            raise GitInputError("ref")
        git_dir, tree = await self._adirs(target)
        result = await self._run(
            ["ls-tree", "-r", "-z", "--long", "--full-tree", commit],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
            truncate=True,
        )
        records = result.stdout.split(b"\0")
        if result.truncated:
            records = records[:-1]  # the last record may be cut
        files: list[TreeFile] = []
        skipped = 0
        for record in records:
            meta, _, raw_path = record.partition(b"\t")
            fields = meta.split()
            if len(fields) != 4 or fields[0] not in (b"100644", b"100755") or fields[1] != b"blob":
                continue  # symlink, submodule, tree, or the empty tail after the last NUL
            try:
                files.append(TreeFile(raw_path.decode("utf-8"), int(fields[3])))
            except (UnicodeDecodeError, ValueError):
                skipped += 1
        return TreeListing(files, result.truncated, skipped)

    async def blob_sizes(self, target: GitTarget, commit: str, paths: list[str]) -> dict[str, int]:
        """The size of each of ``paths`` that is a regular file in ``commit``, read from the object database.
        Exact names only: a pathspec ``config`` also matches ``config/**``, and those entries are dropped."""
        if not _SHA.fullmatch(commit):
            raise GitInputError("ref")
        if not paths:
            return {}
        wanted = {_pathspec(path) for path in paths}
        git_dir, tree = await self._adirs(target)
        result = await self._run(
            ["ls-tree", "-z", "--long", "--full-tree", commit, "--", *sorted(wanted)],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
        )
        sizes: dict[str, int] = {}
        for record in result.stdout.split(b"\0"):
            meta, _, raw_path = record.partition(b"\t")
            fields = meta.split()
            if len(fields) != 4 or fields[1] != b"blob" or not fields[3].isdigit():
                continue  # a tree, a submodule, or the empty tail after the last NUL
            name = raw_path.decode("utf-8", "replace")
            if name in wanted:
                sizes[name] = int(fields[3])
        return sizes

    async def grep(
        self,
        target: GitTarget,
        patterns: list[str],
        *,
        commit: str | None,
        fixed: bool,
        ignore_case: bool,
        only_matching: bool,
        timeout_s: float,
        max_output: int,
        max_matches: int | None = None,
        threads: int | None = None,
        memory_limit: int | None = GREP_MEMORY_LIMIT,
    ) -> GrepResult:
        """``git grep`` over the working tree with untracked files (``commit=None``: ignored files and
        ``.git`` are never read, symlinks are not followed) or over ``commit``'s tree. Patterns follow ``-e``
        (never options); ``fixed``: literal strings, else POSIX ERE. The regex runs inside git, which this
        adapter kills at ``timeout_s`` (``GitError("timeout")``): no pattern can hold the event loop."""
        if not patterns or any(not p or "\0" in p or "\n" in p or "\r" in p for p in patterns):
            raise GitInputError("pattern")
        if commit is not None and not _SHA.fullmatch(commit):
            raise GitInputError("ref")
        git_dir, tree = await self._adirs(target)
        args = ["grep", "-z", "-n", "-I", "--no-color", "-F" if fixed else "-E"]
        if threads is not None:
            args.append(f"--threads={threads}")
        if ignore_case:
            args.append("-i")
        if only_matching:
            args.append("-o")
        if commit is None:
            args += ["--untracked", "--column"]
        for pattern in patterns:
            args += ["-e", pattern]
        args += [commit, "--"] if commit is not None else ["--"]
        result = await self._run(
            args,
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
            # UTF-8 (built into glibc): -i and "." then work on non-ASCII text; messages stay untranslated.
            env={"LC_ALL": "C.UTF-8"},
            check=False,
            truncate=True,
            timeout_s=timeout_s,
            max_output=max_output,
            memory_limit=memory_limit,
        )
        if result.returncode not in (0, 1) and not result.truncated:
            stderr = result.stderr.decode("utf-8", "replace")
            if result.returncode == 128 and stderr.startswith(("fatal: -e option", "fatal: command line")):
                raise GitInputError("regex")
            self._log_failure("grep", result, tree)
            raise GitError("grep_failed")
        # Parsing a few MiB of output is CPU work: off the event loop (AG-13 M2, WP-2.1d).
        return await asyncio.to_thread(
            _parse_grep,
            result.stdout,
            prefix=f"{commit}:".encode() if commit is not None else b"",
            with_column=commit is None,
            truncated=result.truncated,
            max_matches=max_matches,
        )

    # --- write ---------------------------------------------------------------------------------------------

    async def create_branch(self, target: GitTarget, name: str, from_ref: str) -> str:
        _branch(name)
        git_dir, tree = await self._adirs(target)
        sha = await self._resolve(git_dir, tree, from_ref)
        await self._run(["branch", "--no-track", name, sha], git_dir=git_dir, work_tree=tree, cwd=tree)
        return sha

    async def commit_all(self, target: GitTarget, message: str, author: Author) -> str | None:
        """Stage everything (but ``.kk``) and commit; ``None`` when there is nothing to commit."""
        return await self.commit(target, message, author)

    async def commit(
        self,
        target: GitTarget,
        message: str,
        author: Author,
        *,
        paths: list[str] | None = None,
        exclude: tuple[str, ...] = (".kk",),
    ) -> str | None:
        """Stage ``paths`` (everything when ``None``), unstage ``exclude``, and commit; ``None`` when nothing
        is staged. Paths are literal pathspecs (``GIT_LITERAL_PATHSPECS``), never options or magic.

        ``exclude`` defaults to ``.kk``: sandbox artefacts never enter git (SECURITY_MODEL §4), not even
        when an imported archive already tracked them (their index entries go back to HEAD's).
        """
        _message(message)
        _author(author)
        if paths is not None and not paths:
            raise GitInputError("path")
        selected = [_pathspec(path) for path in paths] if paths is not None else []
        excluded = [_pathspec(path) for path in exclude]
        git_dir, tree = await self._adirs(target)
        if target.worktree is None:
            await asyncio.to_thread(_ensure_excludes, git_dir)
        before = await self.current_commit(target)
        born = before is not None
        try:
            # The commit holds exactly what this call stages: whatever an earlier, failed run left in the
            # index is put back to HEAD first.
            await self._reset_index(git_dir, tree, born=born)
            await self._run(["add", "-A", "--", *selected], git_dir=git_dir, work_tree=tree, cwd=tree)
            for path in excluded:
                await self._unstage(git_dir, tree, path, born=born)
            staged = await self._run(
                ["diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv"],
                git_dir=git_dir,
                work_tree=tree,
                cwd=tree,
                check=False,
            )
            if staged.returncode == 0:
                return None
            await self._run(
                ["commit", "-q", "--no-verify", "-m", message],
                git_dir=git_dir,
                work_tree=tree,
                cwd=tree,
                env=_identity_env(author, self.committer),
            )
        except BaseException as exc:
            if _killed_git(exc):
                await asyncio.to_thread(_remove_index_lock, git_dir)
            if not isinstance(exc, Exception):
                raise  # a cancel goes up at once; the next commit puts the index back first
            landed = await self._landed(target, git_dir, tree, before, exc, message=message, author=author)
            if landed is not None:
                return landed
            await self._reset_after_failure(git_dir, tree, born=born)
            raise
        return await self.current_commit(target)

    async def commit_files(self, target: GitTarget, commit: str) -> list[str]:
        """The paths a commit changed (evidence for the audit trail)."""
        if not _SHA.fullmatch(commit):
            raise GitInputError("ref")
        git_dir, tree = await self._adirs(target)
        result = await self._run(
            ["diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", commit],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
        )
        return [name for name in result.stdout.decode("utf-8", "replace").split("\0") if name]

    async def _landed(
        self,
        target: GitTarget,
        git_dir: Path,
        tree: Path,
        before: str | None,
        error: Exception,
        *,
        message: str,
        author: Author,
    ) -> str | None:
        """The new HEAD when a failed commit step still wrote this commit (git stopped after the ref), else
        ``None`` and the original error stands. Evidence over claims: a moved HEAD answers only when it is
        provably this commit (parent ``before``, this author, this message), never another writer's."""
        reason = getattr(error, "reason", None)
        try:
            after = await self.current_commit(target)
            if after is None or after == before:
                return None
            shown = await self._run(
                ["log", "-1", "--format=%P%x00%ae%x00%B", after, "--"],
                git_dir=git_dir,
                work_tree=tree,
                cwd=tree,
            )
        except Exception:
            logger.exception("git_head_unknown_after_failure", reason=reason)
            return None
        parents, email, body = [*shown.stdout.decode("utf-8", "replace").split("\0", 2), "", ""][:3]
        mine = (
            parents.split() == ([before] if before is not None else [])
            and email == author.email
            and body.split() == message.split()  # git trims and collapses the message's whitespace
        )
        if not mine:
            # Another writer moved HEAD: callers must serialise writers of one index (the workspace lock).
            logger.error("git_head_moved_by_another_writer", reason=reason)
            return None
        logger.warning("git_commit_landed_despite_error", reason=reason)
        return after

    async def _reset_index(self, git_dir: Path, tree: Path, *, born: bool) -> None:
        """Put the whole index back to HEAD (empty before the first commit); no-op when nothing is staged."""
        staged = await self._run(
            ["diff", "--cached", "--quiet", "--no-ext-diff", "--no-textconv"],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
            check=False,
        )
        if staged.returncode == 0:
            return
        # ``rm --cached`` refuses an entry that differs from both the file and HEAD; ``read-tree`` does not.
        command = ["restore", "--staged", "--", "."] if born else ["read-tree", "--empty"]
        await self._run(command, git_dir=git_dir, work_tree=tree, cwd=tree)

    async def _reset_after_failure(self, git_dir: Path, tree: Path, *, born: bool) -> None:
        """Leave nothing staged by a failed commit. A failure here is logged, not raised over the commit's own
        error: the next commit resets the index before it stages anything."""
        try:
            await self._reset_index(git_dir, tree, born=born)
        except BaseException as exc:
            if _killed_git(exc):
                await asyncio.to_thread(_remove_index_lock, git_dir)
            if not isinstance(exc, Exception):
                raise
            logger.warning("git_index_reset_failed", reason=getattr(exc, "reason", type(exc).__name__))

    async def _unstage(self, git_dir: Path, tree: Path, path: str, *, born: bool) -> None:
        """Put ``path``'s index entries back to HEAD (none at all before the first commit). Nothing to do
        when the index has none: ``restore`` would fail on a pathspec that matches nothing."""
        listed = await self._run(["ls-files", "-z", "--", path], git_dir=git_dir, work_tree=tree, cwd=tree)
        if not listed.stdout:
            return
        if born:
            command = ["restore", "--staged", "--", path]
        else:
            command = ["rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", path]
        await self._run(command, git_dir=git_dir, work_tree=tree, cwd=tree)

    async def create_worktree(self, repo: Path, path: Path, branch: str, base: str) -> GitTarget:
        """A worktree on a new ``branch`` from ``base`` (ADR-016: ``kk/task-<8hex>``)."""
        _branch(branch)
        if not path.is_absolute():
            raise GitInputError("path")
        git_dir, tree = await self._adirs(GitTarget(repo))
        sha = await self._resolve(git_dir, tree, base)
        if await asyncio.to_thread(os.path.lexists, path):
            raise GitError("worktree_exists")
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await self._run(
            ["worktree", "add", "-q", "-b", branch, str(path), sha],
            git_dir=git_dir,
            work_tree=None,
            cwd=repo,
        )
        await asyncio.to_thread(_ensure_excludes, git_dir)
        target = GitTarget(repo, path)
        await self._adirs(target)  # registered, or not_a_worktree
        return target

    async def remove_worktree(self, repo: Path, path: Path) -> None:
        """Remove a task worktree (dirty or not); its branch stays for the audit trail."""
        if not path.is_absolute():
            raise GitInputError("path")
        # Registered worktrees only; a tampered .git file must not stop the cleanup.
        git_dir, _ = await self._adirs(GitTarget(repo, path), verify_gitfile=False)
        common = repo / ".git"
        try:
            await self._run(
                ["worktree", "remove", "--force", str(path)],
                git_dir=common,
                work_tree=None,
                cwd=repo,
            )
        except GitError:
            # Sandboxed code may have broken <worktree>/.git so git refuses; the folder is ours to delete.
            await asyncio.to_thread(shutil.rmtree, path)
            await self._run(["worktree", "prune"], git_dir=common, work_tree=None, cwd=repo)
        if await asyncio.to_thread(git_dir.exists):
            raise GitError("worktree_not_removed")

    async def merge_branch(
        self, repo: Path, branch: str, message: str, author: Author, *, into: str
    ) -> MergeResult:
        """Merge ``branch`` into ``into`` (the checked-out branch of the main working tree), ``--no-ff``.

        A conflict is aborted, never left in the tree. The tree must be on ``into`` and clean.
        """
        _branch(branch)
        _branch(into)
        _message(message)
        _author(author)
        target = GitTarget(repo)
        git_dir, tree = await self._adirs(target)
        status = await self.status(target)
        if status.branch != into:
            raise GitError("wrong_branch")  # never merge into whatever happens to be checked out
        if any(change.status != "untracked" for change in status.changes):
            return MergeResult(False, None, [], "dirty")
        before = status.head
        result = await self._run(
            ["merge", "--no-ff", "--no-edit", "--no-verify", "-m", message, f"refs/heads/{branch}"],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
            env=_identity_env(author, self.committer),
            check=False,
        )
        if result.returncode == 0:
            after = await self.current_commit(target)
            if after == before:
                return MergeResult(False, None, [], "up_to_date")
            return MergeResult(True, after, [], "merged")
        unmerged = await self._run(
            ["diff", "--name-only", "--diff-filter=U", "-z"],
            git_dir=git_dir,
            work_tree=tree,
            cwd=tree,
        )
        conflicts = [p.decode("utf-8", "surrogateescape") for p in unmerged.stdout.split(b"\0") if p]
        if await asyncio.to_thread((git_dir / "MERGE_HEAD").exists):
            await self._run(["merge", "--abort"], git_dir=git_dir, work_tree=tree, cwd=tree)
        if conflicts:
            return MergeResult(False, None, sorted(conflicts), "conflict")
        if b"would be overwritten" in result.stderr:
            return MergeResult(False, None, [], "dirty")
        self._log_failure("merge", result, tree)
        raise GitError("merge_failed")


def _parse_grep(
    data: bytes, *, prefix: bytes, with_column: bool, truncated: bool, max_matches: int | None = None
) -> GrepResult:
    """``git grep -z -n [--column]`` output, one record per match: ``<prefix><path>\\0<line>\\0`` then
    ``[<column>\\0]<text>\\n``. Paths are printed verbatim (they may hold ``:``, spaces, even newlines), so
    records are read field by field. A record cut by the output cap is dropped."""
    matches: list[GrepMatch] = []
    skipped = 0
    position = 0
    fields = 3 if with_column else 2
    while position < len(data):
        if max_matches is not None and len(matches) >= max_matches:
            truncated = True
            break
        parts: list[bytes] = []
        for _ in range(fields):
            end = data.find(b"\0", position)
            if end < 0:
                break
            parts.append(data[position:end])
            position = end + 1
        end = data.find(b"\n", position)
        if len(parts) < fields or end < 0:
            break  # incomplete last record (output cap)
        text_bytes = data[position:end]
        position = end + 1
        raw_path = parts[0].removeprefix(prefix)
        try:
            path = raw_path.decode("utf-8")
            line = int(parts[1])
            byte_column = int(parts[2]) if with_column else None
        except (UnicodeDecodeError, ValueError):
            skipped += 1
            continue
        # git counts bytes; the API counts characters (the column of the first match, 1-based).
        column = (
            len(text_bytes[: byte_column - 1].decode("utf-8", "replace")) + 1
            if byte_column is not None
            else None
        )
        matches.append(GrepMatch(path, line, column, text_bytes.decode("utf-8", "replace")))
    return GrepResult(matches, truncated, skipped)


def _gitfile_points_at(gitfile: str, registered: Path) -> bool:
    """``<worktree>/.git`` is a regular file reading ``gitdir: <registered git dir>``."""
    try:
        if not stat.S_ISREG(os.lstat(gitfile).st_mode):
            return False
        with open(gitfile, "rb") as handle:
            content = handle.read(4097).decode("utf-8", "replace").strip()
    except OSError:
        return False
    if not content.startswith("gitdir: "):
        return False
    return os.path.realpath(content.removeprefix("gitdir: ")) == os.path.realpath(registered)


def _ensure_excludes(git_dir: Path) -> None:
    """Keep the workspace's temp files and the sandbox's ``.kk/`` out of every commit, and out of status
    (``info/exclude`` covers all worktrees)."""
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text() if exclude.exists() else ""
    missing = [pattern for pattern in _EXCLUDE_PATTERNS if pattern not in current.splitlines()]
    if missing:
        separator = "" if current.endswith("\n") or not current else "\n"
        with exclude.open("a") as handle:
            handle.write(separator + "".join(f"{pattern}\n" for pattern in missing))


def _killed_git(exc: BaseException) -> bool:
    """``_run`` kills git (SIGKILL) on a timeout, past the output cap, and on a cancel: an ``index.lock`` git
    held is left behind, and every later commit fails until it is gone. Callers serialise writers of one index
    (the workspace lock), so that lock is ours to remove."""
    if isinstance(exc, asyncio.CancelledError):
        return True
    return isinstance(exc, GitError) and exc.reason in ("timeout", "output_too_large")


def _remove_index_lock(git_dir: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        (git_dir / "index.lock").unlink()
        logger.error("git_index_lock_removed", git_dir=git_dir.name)


async def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    # A reader that stopped early (output cap) may leave the stdout transport paused: then the pipe's EOF is
    # never read and wait() blocks until the caller's timeout (AG-13 L2, WP-2.1d). Drain what is left; the
    # process group is dead, so EOF comes at once.
    if proc.stdout is not None:
        with contextlib.suppress(Exception):
            while await proc.stdout.read(_CHUNK):
                pass
    await proc.wait()
