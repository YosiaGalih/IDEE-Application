"""`security.scan_secrets` over the task worktree (spec 3.1d2a Goal 11; SECURITY_MODEL T5)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tools.builtin import security
from app.workspace.fs import TaskWorkspace
from tests.integration import test_gateway_denials as denials
from tests.integration.test_gateway_denials import Scope
from tests.tool_fakes import context, gateway
from tests.worktree_fakes import task_worktree, write

clean = denials.clean
sessions = denials.sessions
scope = denials.scope
KEY = "sk-ant-api03-" + "Q" * 40  # synthetic  # gitleaks:allow


async def scan(sessions: async_sessionmaker[AsyncSession], scope: Scope, ws: TaskWorkspace,
               **raw: object) -> str:  # fmt: skip
    built = gateway(sessions, *security.specs())
    tools = ["security.scan_secrets"]
    ctx = context(task_id=scope.task_id, project_id=scope.project_id, tools=tools, workspace=ws)
    result = await built.execute(ctx, "security.scan_secrets", raw)
    return f"{result.status}|{result.content}"


async def worktree(tmp_path: Path) -> TaskWorkspace:
    ws = await task_worktree(
        tmp_path,
        {"src/clean.py": "x = 1\n", ".gitignore": "local.secret.txt\n"},
        repo_agentignore="private/\n",
    )
    write(ws.root / "src" / "config.py", f"import os\n\nAPI = '{KEY}'\npassword = 'hunter22'\n")
    write(ws.root / ".env", f"ANTHROPIC_API_KEY={KEY}\n")  # untracked: would be committed
    write(ws.root / "config" / "prod.env", "DB=x\n")
    write(ws.root / "local.secret.txt", f"{KEY}\n")  # gitignored: never committed, not scanned
    write(ws.root / "private" / "notes.txt", f"{KEY}\n")  # hidden from agents by repo/.agentignore
    return ws


async def test_findings_are_paths_lines_and_rules_never_values(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    out = await scan(sessions, scope, await worktree(tmp_path))
    assert out.startswith('ok|<untrusted_content source="security.scan_secrets">'), out
    assert KEY not in out and "hunter22" not in out
    body = out.split("\n")[1:-1]
    assert "src/config.py:3: anthropic_key" in body
    assert "src/config.py:4: credential_assignment" in body
    assert ".env: secret_file" in body and "config/prod.env: secret_file" in body
    assert not any("local.secret.txt" in line or "private/" in line for line in body), body
    assert "[1 files not scanned: agents may not read them]" in body  # counted as hidden, never shown
    assert not any(line.startswith("src/clean.py") for line in body)


async def test_paths_narrow_the_scan(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    out = await scan(sessions, scope, await worktree(tmp_path), paths=["src"])
    body = out.split("\n")[1:-1]
    assert "src/config.py:3: anthropic_key" in body and not any(".env" in line for line in body)


async def test_a_clean_worktree_says_so(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    ws = await task_worktree(tmp_path, {"src/a.py": "x = 1\n"})
    out = await scan(sessions, scope, ws)
    assert "no secrets found" in out


async def test_scan_reports_what_it_did_not_scan(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path, monkeypatch: object
) -> None:
    """Silent-failure H1/M1 and AG-13 LOW-6: a name that is not UTF-8, a byte budget, a scope that matches
    nothing: each is said, and "no secrets" is only claimed for what was scanned."""
    import os

    ws = await task_worktree(tmp_path, {"src/a.py": "x = 1\n", "src/b.py": "y = 2\n"})
    (ws.root / os.fsdecode(b"creds\xff.pem")).write_text(f"{KEY}\n")
    out = await scan(sessions, scope, ws)
    assert "[1 files with names that are not UTF-8: not scanned]" in out, out
    assert "[no secrets found in 3 scanned files]" in out, out  # README.md, src/a.py, src/b.py
    nothing = await scan(sessions, scope, ws, paths=["typo"])
    assert "[nothing scanned: no file within paths]" in nothing, nothing


async def test_the_byte_budget_is_said(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    import pytest

    ws = await task_worktree(tmp_path, {"src/a.py": "x = 1\n" * 100, "src/b.py": f"k = '{KEY}'\n"})
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(security, "MAX_SCAN_BYTES", 50)
        out = await scan(sessions, scope, ws)
    assert "not scanned: over the byte budget of one scan" in out, out
    assert "no secrets found" not in out or "scanned" in out


async def test_matching_runs_off_the_event_loop(
    sessions: async_sessionmaker[AsyncSession], scope: Scope, tmp_path: Path
) -> None:
    """Backend review HIGH: Redactor.find is CPU work per file; it must not hold the loop."""
    import asyncio
    import time

    import pytest

    from app.core.redaction import Redactor

    ws = await task_worktree(tmp_path, {"src/a.py": "x = 1\n"})
    real = Redactor.find

    def slow(self: Redactor, text: str) -> object:
        time.sleep(0.6)
        return real(self, text)

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Redactor, "find", slow)
        task = asyncio.ensure_future(ticker())
        try:
            await scan(sessions, scope, ws)
        finally:
            task.cancel()
    assert ticks >= 15, ticks  # ~1.2 s of slow matching; a blocked loop would tick only between files
