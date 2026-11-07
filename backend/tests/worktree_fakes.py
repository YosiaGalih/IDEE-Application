"""A real task worktree for builtin tests: repo/ with a first commit, tasks/<id> on branch kk/task-*."""

from __future__ import annotations

from pathlib import Path

from app.gitops.adapter import Author, GitAdapter, GitTarget
from app.workspace.fs import TaskWorkspace

ME = Author("Test", "test@example.com")


def write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text)


async def task_worktree(
    tmp_path: Path, committed: dict[str, str], *, repo_agentignore: str | None = None
) -> TaskWorkspace:
    git = GitAdapter()
    repo = tmp_path / "p" / "repo"
    repo.mkdir(parents=True)
    await git.init(repo, "main")
    for rel, text in {"README.md": "# project\n", **committed}.items():
        write(repo / rel, text)
    if repo_agentignore is not None:
        write(repo / ".agentignore", repo_agentignore)
    base = await git.commit_all(GitTarget(repo), "init", ME)
    assert base is not None
    root = tmp_path / "p" / "tasks" / "t1"
    await git.create_worktree(repo, root, "kk/task-00000001", base)
    return TaskWorkspace(root, repo=repo, branch="kk/task-00000001", base_commit=base)
