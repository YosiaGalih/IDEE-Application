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
    if not 1 <= len(message) <= 5000 or _MESSAGE_FORBIDDEN.search(message):
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


def _identity_env(author: Author, committer: Author) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": author.name,
        "GIT_AUTHOR_EMAIL": author.email,
        "GIT_COMMITTER_NAME": committer.name,
        "GIT_COMMITTER_EMAIL": committer.email,
    }


class GitAdapter:
    def __init__(
        self,
        git: str = "git",
        *,
        timeout_s: float = 60.0,  # [Menebak]
        max_output: int = 16 * 1024 * 1024,  # [Menebak]
        committer: Author = Author("KodeKantor", "kodekantor@localhost"),  # noqa: B008 - frozen value
    ) -> None:
        self.git = git
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
    ) -> _Result:
        """Run git. ``truncate``: past ``max_output`` stop reading and return what was read (no error)."""
        argv = [self.git]
        for setting in _CONFIG:
            argv += ["-c", setting]
        if git_dir is not None:
            argv.append(f"--git-dir={git_dir}")
        if work_tree is not None:
            argv.append(f"--work-tree={work_tree}")
        argv += args
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env={**_BASE_ENV, **(env or {})},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # its own process group, so a timeout kills every child too
        )
        try:
            stdout, stderr, cut = await asyncio.wait_for(self._collect(proc, truncate), self.timeout_s)
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

    async def _collect(self, proc: asyncio.subprocess.Process, truncate: bool) -> tuple[bytes, bytes, bool]:
        assert proc.stdout is not None and proc.stderr is not None
        stdout_stream, stderr_stream = proc.stdout, proc.stderr

        async def out() -> tuple[bytes, bool]:
            chunks: list[bytes] = []
            size = 0
            while chunk := await stdout_stream.read(_CHUNK):
                if size + len(chunk) > self.max_output:
                    if not truncate:
                        raise _TooLarge()
                    chunks.append(chunk[: self.max_output - size])
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
    ) -> Diff:
        """``base``..``head``; ``head=None`` is the working tree including untracked files."""
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
        with tempfile.TemporaryDirectory(prefix="kk-git-diff-") as scratch:
            # A copy of the index and a scratch object store: untracked files are staged for the diff, and the
            # blobs that creates land in the scratch store, so reading a diff never writes to the repository.
            index = Path(scratch) / "index"
            objects = Path(scratch) / "objects"
            await asyncio.to_thread(objects.mkdir)
            with contextlib.suppress(FileNotFoundError):
                await asyncio.to_thread(shutil.copyfile, git_dir / "index", index)
            env = {
                "GIT_INDEX_FILE": str(index),
                "GIT_OBJECT_DIRECTORY": str(objects),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(target.repo / ".git" / "objects"),
            }
            await self._run(["add", "-A"], git_dir=git_dir, work_tree=tree, cwd=tree, env=env)
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

    # --- write ---------------------------------------------------------------------------------------------

    async def create_branch(self, target: GitTarget, name: str, from_ref: str) -> str:
        _branch(name)
        git_dir, tree = await self._adirs(target)
        sha = await self._resolve(git_dir, tree, from_ref)
        await self._run(["branch", "--no-track", name, sha], git_dir=git_dir, work_tree=tree, cwd=tree)
        return sha

    async def commit_all(self, target: GitTarget, message: str, author: Author) -> str | None:
        """Stage everything and commit; ``None`` when there is nothing to commit."""
        _message(message)
        _author(author)
        git_dir, tree = await self._adirs(target)
        await self._run(["add", "-A"], git_dir=git_dir, work_tree=tree, cwd=tree)
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
        return await self.current_commit(target)

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
    """Keep the workspace's own temp files out of every commit (``info/exclude`` covers all worktrees)."""
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    current = exclude.read_text() if exclude.exists() else ""
    if WORKSPACE_TEMP_PATTERN not in current.splitlines():
        separator = "" if current.endswith("\n") or not current else "\n"
        with exclude.open("a") as handle:
            handle.write(f"{separator}{WORKSPACE_TEMP_PATTERN}\n")


async def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    await proc.wait()
