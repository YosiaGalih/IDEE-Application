"""A policy refusal raised by a handler is a denial (AG-13 3.1d2b LOW-2; spec WP-3.1f Keputusan Lead #31).

The gateway refuses most calls before any handler runs. Some refusals can only be made by the handler, which
sees the worktree: a path agents may not read or write (``path_denied``), a delete without scoped_write
(``delete_not_allowed``), a commit off the task branch (``not_task_branch``), a commit that would change
hidden paths (``hidden_paths_affected``). These end ``denied`` with one audit ``tool.denied``, like the
gateway's own refusals, never ``error``. Other handler errors (a missing file, a bad patch) stay ``error``.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tools.base import ToolContext, ToolError, ToolOutput
from app.tools.builtin import code, commit, repo
from app.tools.gateway import DENIALS, HANDLER_DENIALS
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope, audits, calls, timeline
from tests.tool_fakes import context, gateway, spec
from tests.worktree_fakes import task_worktree, write

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
_uses = itertools.count()


def test_the_policy_codes_are_the_builtins_refusals() -> None:
    assert set(HANDLER_DENIALS) == {
        "path_denied",
        "delete_not_allowed",
        "not_task_branch",
        "hidden_paths_affected",
    }
    assert all(code in DENIALS for code in HANDLER_DENIALS)  # a fixed text for the model, never an echo


async def assert_handler_denied(
    sessions: async_sessionmaker[AsyncSession], result: Any, ctx: ToolContext, reason: str
) -> None:
    assert (result.status, result.is_error, result.content) == ("denied", True, DENIALS[reason])
    (row,) = [r for r in await calls(sessions, ctx.task_id) if r.id == result.tool_call_id]
    assert (row.status, row.error_code, row.output_summary) == ("denied", reason, DENIALS[reason])
    audited = await audits(sessions, row)
    assert [(a.action, a.result, a.actor_type) for a in audited] == [("tool.denied", "denied", "agent")]
    assert audited[0].metadata_["reason"] == reason
    events = [e for e in await timeline(sessions, ctx.task_id) if e.payload["tool_call_id"] == str(row.id)]
    assert [e.type for e in events] == ["tool.call_started", "tool.call_finished"]
    assert events[-1].payload["status"] == "denied"


def _raising(code_: str) -> Any:
    async def handler(ctx: ToolContext, validated: Any) -> ToolOutput:
        raise ToolError(code_, "refused by the handler")

    return handler


@pytest.mark.parametrize(
    "reason", sorted(["path_denied", "delete_not_allowed", "not_task_branch", "hidden_paths_affected"])
)
@pytest.mark.parametrize("tool", ["test.read", "test.write"])
async def test_every_policy_refusal_from_a_handler_is_denied(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, reason: str, tool: str
) -> None:
    built = gateway(sessions, spec(tool, _raising(reason)))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=[tool])
    raw = {"path": "a.txt"}
    result = await built.execute(ctx, tool, raw, tool_use_id=f"toolu_h_{next(_uses)}")
    await assert_handler_denied(sessions, result, ctx, reason)


@pytest.mark.parametrize("reason", ["not_found", "invalid_patch", "busy", "git_failed"])
async def test_other_handler_errors_stay_errors(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, reason: str
) -> None:
    built = gateway(sessions, spec("test.write", _raising(reason)))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"])
    result = await built.execute(ctx, "test.write", {"path": "a.txt"}, tool_use_id="toolu_e")
    assert (result.status, result.is_error) == ("error", True)
    (row,) = await calls(sessions, scope.task_id)
    assert (row.status, row.error_code) == ("error", reason)
    assert [a.action for a in await audits(sessions, row)] == ["tool.executed"]


async def test_a_replayed_handler_denial_stays_denied(
    sessions: async_sessionmaker[AsyncSession], scope: Scope
) -> None:
    built = gateway(sessions, spec("test.write", _raising("path_denied")))
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["test.write"])
    first = await built.execute(ctx, "test.write", {"path": "a.txt"}, tool_use_id="toolu_same")
    again = await built.execute(ctx, "test.write", {"path": "a.txt"}, tool_use_id="toolu_same")
    assert (first.status, again.status, again.is_error) == ("denied", "denied", True)
    assert len(await calls(sessions, scope.task_id)) == 1


def test_a_handler_denial_counts_like_any_denial() -> None:
    """Denied calls count neither against a tool quota nor the budget's tool iterations
    (``UNCOUNTED_TOOL_STATUSES``); one rule for every denial."""
    from app.workflow.budget import UNCOUNTED_TOOL_STATUSES

    assert "denied" in UNCOUNTED_TOOL_STATUSES


# --- the builtins themselves --------------------------------------------------------------------------------


@pytest.fixture
async def ws(tmp_path: Path) -> TaskWorkspace:
    workspace = await task_worktree(tmp_path, {"src/a.py": "a\n", "tests/test_old.py": "old\n"})
    write(workspace.root / ".env", "TOKEN=kk-dot-env-secret\n")
    return workspace


async def test_reading_dot_env_is_denied(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    built = gateway(sessions, *repo.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["repo.read_file"], workspace=ws)
    result = await built.execute(ctx, "repo.read_file", {"path": ".env"}, tool_use_id="toolu_env")
    await assert_handler_denied(sessions, result, ctx, "path_denied")
    assert "kk-dot-env-secret" not in result.content


async def test_writing_dot_env_is_denied(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    built = gateway(sessions, *code.specs())
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=["code.write_file"], workspace=ws)
    result = await built.execute(
        ctx, "code.write_file", {"path": ".env", "content": "TOKEN=x\n"}, tool_use_id="toolu_wenv"
    )
    await assert_handler_denied(sessions, result, ctx, "path_denied")
    assert (ws.root / ".env").read_text() == "TOKEN=kk-dot-env-secret\n"


async def test_a_patch_that_deletes_without_scoped_write_is_denied(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace
) -> None:
    built = gateway(sessions, *code.specs())
    ctx = context(
        task_id=scope.task_id,
        project_id=scope.project_id,
        tools=["code.apply_patch"],
        workspace=ws,
        filesystem="test_only",
    )
    diff = "--- a/tests/test_old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-old\n"
    result = await built.execute(ctx, "code.apply_patch", {"diff": diff}, tool_use_id="toolu_del")
    await assert_handler_denied(sessions, result, ctx, "delete_not_allowed")
    assert (ws.root / "tests" / "test_old.py").exists()


async def test_a_commit_off_the_task_branch_is_denied(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    workspace = await task_worktree(tmp_path, {"src/a.py": "a\n"})
    off_branch = TaskWorkspace(
        workspace.root, repo=workspace.repo, branch="feature/x", base_commit=workspace.base_commit
    )
    built = gateway(sessions, *commit.specs())
    ctx = context(
        task_id=scope.task_id, project_id=scope.project_id, tools=["git.commit_task"], workspace=off_branch
    )
    result = await built.execute(ctx, "git.commit_task", {"message": "x"}, tool_use_id="toolu_commit")
    await assert_handler_denied(sessions, result, ctx, "not_task_branch")
