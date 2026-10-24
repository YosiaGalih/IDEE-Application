"""Repository content never runs code in the control plane through git (CLAUDE.md #3, T3; snapshot finding
".git/config → eksekusi host")."""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.gitops import adapter as adapter_module
from app.gitops.adapter import Author, GitAdapter, GitTarget
from app.gitops.errors import GitError, GitInputError

ME = Author("Dev", "dev@example.test")
ADAPTER_SOURCE = Path(adapter_module.__file__)


def evil_script(tmp: Path, marker: Path) -> Path:
    script = tmp / "evil.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    script.chmod(0o755)
    return script


def git_config(git_dir: Path, key: str, value: str) -> None:
    """Write a repo config value directly, as an attacker with write access to .git would."""
    subprocess.run(["git", "--git-dir", str(git_dir), "config", key, value], check=True)  # noqa: S603, S607


def make_evil_repo(tmp: Path, script: Path) -> Path:
    """A bare repo whose config runs ``script`` through filters, fsmonitor and hooks."""
    evil = tmp / "evil.git"
    subprocess.run(["git", "init", "-q", "--bare", str(evil)], check=True)  # noqa: S603, S607 - test fixture
    for key, value in (
        ("filter.evil.clean", str(script)),
        ("filter.evil.smudge", str(script)),
        ("filter.evil.required", "true"),
        ("core.fsmonitor", str(script)),
        ("core.hooksPath", str(tmp)),
    ):
        subprocess.run(["git", "--git-dir", str(evil), "config", key, value], check=True)  # noqa: S603, S607
    return evil


@pytest.fixture
async def repo(tmp_path: Path) -> GitTarget:
    root = tmp_path / "p" / "repo"
    root.mkdir(parents=True)
    git = GitAdapter()
    await git.init(root, "main")
    (root / "f.txt").write_text("a\n")
    await git.commit_all(GitTarget(root), "init", ME)
    return GitTarget(root)


async def test_hooks_never_run(tmp_path: Path, repo: GitTarget) -> None:
    marker = tmp_path / "hook-ran"
    hooks = repo.repo / ".git" / "hooks"
    for name in ("pre-commit", "post-commit", "post-checkout", "post-merge", "reference-transaction"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / name).chmod(0o755)
    git = GitAdapter()
    (repo.repo / "f.txt").write_text("b\n")
    await git.commit_all(repo, "change", ME)
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-0000000a", base)
    (task.worktree / "g.txt").write_text("g\n")  # type: ignore[operator]
    await git.commit_all(task, "task", ME)
    await git.merge_branch(repo.repo, "kk/task-0000000a", "apply", ME, into="main")
    assert not marker.exists()


async def test_forged_worktree_gitdir_is_refused(tmp_path: Path, repo: GitTarget) -> None:
    """Sandboxed code rewrites ``<worktree>/.git`` to a repo whose config runs commands (ADR-026 (4)).

    The adapter refuses the worktree (``git_metadata_unsafe``) and nothing from the evil repo runs; the task's
    worktree can still be removed.
    """
    marker = tmp_path / "pwned"
    script = evil_script(tmp_path, marker)
    git = GitAdapter()
    base = await git.current_commit(repo)
    assert base is not None
    task = await git.create_worktree(repo.repo, repo.repo.parent / "tasks" / "t1", "kk/task-0000000b", base)
    assert task.worktree is not None
    evil = make_evil_repo(tmp_path, script)
    (task.worktree / ".git").write_text(f"gitdir: {evil}\n")
    (task.worktree / ".gitattributes").write_text("* filter=evil\n")
    (task.worktree / "f.txt").write_text("changed by the task\n")

    for call in (git.status(task), git.diff(task), git.commit_all(task, "task", ME)):
        with pytest.raises(GitError) as caught:
            await call
        assert caught.value.reason == "git_metadata_unsafe"
    assert not marker.exists()
    await git.remove_worktree(repo.repo, task.worktree)
    assert not task.worktree.exists()
    assert not marker.exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("core.fsmonitor", "touch pwned"),
        ("core.hooksPath", "hooks-elsewhere"),
        ("core.sshCommand", "touch pwned"),
        ("filter.lfs.smudge", "touch pwned"),
        ("diff.external", "touch pwned"),
        ("include.path", "evil.cfg"),
        ("alias.st", "!touch pwned"),
        ("extensions.worktreeConfig", "true"),
        ("core.worktree", "/"),
    ],
)
async def test_unsafe_repository_config_is_refused(repo: GitTarget, key: str, value: str) -> None:
    git_config(repo.repo / ".git", key, value)
    with pytest.raises(GitError) as caught:
        await GitAdapter().status(repo)
    assert caught.value.reason == "git_metadata_unsafe"


async def test_object_alternates_are_refused(tmp_path: Path, repo: GitTarget) -> None:
    (repo.repo / ".git" / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (repo.repo / ".git" / "objects" / "info" / "alternates").write_text(
        str(tmp_path / "other.git/objects") + "\n"
    )
    with pytest.raises(GitError) as caught:
        await GitAdapter().log(repo)
    assert caught.value.reason == "git_metadata_unsafe"


async def test_clone_style_config_is_allowed(repo: GitTarget) -> None:
    for key, value in (
        ("remote.origin.url", "https://example.test/x.git"),
        ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
        ("branch.main.remote", "origin"),
        ("branch.main.merge", "refs/heads/main"),
    ):
        git_config(repo.repo / ".git", key, value)
    assert (await GitAdapter().status(repo)).branch == "main"


async def test_worktree_must_belong_to_repo(tmp_path: Path, repo: GitTarget) -> None:
    stray = tmp_path / "stray"
    stray.mkdir()
    with pytest.raises(GitError) as caught:
        await GitAdapter().status(GitTarget(repo.repo, stray))
    assert caught.value.reason == "not_a_worktree"


async def test_env_and_argv_are_fixed(monkeypatch: pytest.MonkeyPatch, repo: GitTarget) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
    real = asyncio.create_subprocess_exec

    async def spy(*argv: str, **kwargs: Any) -> asyncio.subprocess.Process:
        calls.append((argv, kwargs))
        return await real(*argv, **kwargs)

    monkeypatch.setenv("KK_SECRET_PROBE", "should-not-leak")
    monkeypatch.setattr(adapter_module.asyncio, "create_subprocess_exec", spy)
    await GitAdapter().status(repo)
    argv, kwargs = calls[0]
    assert argv[0] == "git"
    assert "shell" not in kwargs
    env = kwargs["env"]
    assert "KK_SECRET_PROBE" not in env
    assert set(env) <= {
        "PATH",
        "HOME",
        "LC_ALL",
        "GIT_TERMINAL_PROMPT",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_ATTR_NOSYSTEM",
        "GIT_OPTIONAL_LOCKS",
        "GIT_ASKPASS",
        "GIT_EDITOR",
        "GIT_LITERAL_PATHSPECS",
    }
    assert (env["GIT_CONFIG_NOSYSTEM"], env["GIT_CONFIG_GLOBAL"], env["GIT_TERMINAL_PROMPT"]) == (
        "1",
        "/dev/null",
        "0",
    )
    flags = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    for required in (
        "core.hooksPath=/dev/null",
        "protocol.allow=never",
        "protocol.https.allow=always",
        "core.fsmonitor=false",
        "credential.helper=",
        "submodule.recurse=false",
    ):
        assert required in flags
    assert f"--git-dir={repo.repo / '.git'}" in argv
    assert kwargs.get("start_new_session") is True


@pytest.mark.parametrize(
    "branch",
    [
        "-x",
        "a..b",
        "a@{b}",
        "a//b",
        "x.lock",
        "a/",
        "a.",
        ".hidden",
        "a b",
        "a\nb",
        "",
        "a" * 201,
        "a~1",
        "a^",
        "a:b",
    ],
)
async def test_rejects_bad_branch_names(repo: GitTarget, branch: str) -> None:
    with pytest.raises(GitInputError):
        await GitAdapter().create_branch(repo, branch, "main")


@pytest.mark.parametrize("ref", ["--all", "HEAD~1", "main^", "../x", "", "main@{1}", "a b"])
async def test_rejects_bad_refs(repo: GitTarget, ref: str) -> None:
    with pytest.raises(GitInputError):
        await GitAdapter().create_branch(repo, "ok-branch", ref)


@pytest.mark.parametrize(
    ("message", "author"),
    [
        ("", ME),
        ("a\x00b", ME),
        ("x" * 5001, ME),
        ("subject\x1ewith a record separator", ME),
        ("bell\x07", ME),
        ("ok", Author("Ev<il", "e@x.test")),
        ("ok", Author("Evil", "e@x.test>\nInjected: yes")),
        ("ok", Author("", "e@x.test")),
    ],
)
async def test_rejects_bad_commit_input(repo: GitTarget, message: str, author: Author) -> None:
    with pytest.raises(GitInputError):
        await GitAdapter().commit_all(repo, message, author)


@pytest.mark.parametrize("path", ["/etc/passwd", "../x", "a\x00b", ":(glob)*", "-p"])
async def test_rejects_bad_pathspecs(repo: GitTarget, path: str) -> None:
    if path.startswith((":", "-")):  # literal pathspecs: harmless names, not magic or options
        await GitAdapter().diff(repo, paths=[path])
        return
    with pytest.raises(GitInputError):
        await GitAdapter().diff(repo, paths=[path])


def test_no_dangerous_subcommands() -> None:
    literals = {
        node.value
        for node in ast.walk(ast.parse(ADAPTER_SOURCE.read_text()))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for forbidden in ("push", "fetch", "clone", "reset", "clean", "--hard", "-f", "--force-with-lease"):
        assert forbidden not in literals
    assert ADAPTER_SOURCE.read_text().count('"--force"') == 1  # only `worktree remove --force`
