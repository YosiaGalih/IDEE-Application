"""T5 at the agent-run level (SECURITY_MODEL T5; spec WP-3.1f Keputusan Lead #29; plan WP-3.1f2a Task 10).

**Scope.** One agent run through the production composition (``build_agent_runtime``: real provider gate,
budget, ledger, lease, run store, events and the production tool gateway with its builtins); only the provider
is scripted. Secrets planted in the worker's env, the repository, the agent's input and the model's own
output never reach a provider request, ``agent_runs``, ``tool_calls``, ``usage_records``, ``task_events``,
``audit_events``, the bus (``agent.delta`` included) or the logs. This is the agent-run level version: it is
extended in WP-3.2, 4.1 and 5.1 and is **not** the full AC-15/T1 evidence.

The model's own earlier output goes back to the provider as it was (plan Temuan #12): the model only ever saw
redacted data, so only a secret the scripted model itself wrote (the PEM of its ``code.write_file``) can be
in that history. Everything else in every request is checked for every secret.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from structlog.testing import capture_logs

from app.agents.contracts import ImplementInput
from app.core.audit import AuditWriter
from app.core.config import Settings
from app.core.redaction import Redactor
from app.llm.base import LLMRequest, TextBlock, ToolResultBlock, ToolUseBlock
from app.llm.pricing import PricingTable
from app.llm.scenarios.model import Reply
from app.llm.scripted_provider import ScriptedProvider
from app.models.agents import AgentRun
from app.models.tools import ToolCall
from app.services.agent_runtime import build_agent_runtime
from tests.conftest import reset_data
from tests.integration.test_task_schema import make_project, make_task
from tests.runtime_fakes import (
    OUTPUTS,
    REGISTRY,
    STEP,
    WORKER_ID,
    FakeProviders,
    RecordingBus,
    hold_lease,
    run_request,
    scripted,
    tool_turn,
)
from tests.security.test_runtime_gate import record, worker
from tests.worktree_fakes import task_worktree

# Synthetic secrets, assembled so that no source line is a whole secret (gitleaks, PR #25/#26).
KEY = "sk-ant-" + "test-" + "0" * 40 + "a1b2"
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEow" + "IBAAKCAQEA" * 6 + "\n-----END RSA PRIVATE KEY-----"
AWS = "AKIA" + "QWERTYUIOPASDFGH"
URL = "https://" + "deploy:s3cr3tpw" + "@git.example.test/x"
PASSWORD = "hunter" + "2hunter2hunter2"
TOKEN = "tok" + "en-value-" + "9" * 20
SECRETS = {"key": KEY, "pem": PEM, "aws": AWS, "url": URL, "password": PASSWORD, "token": TOKEN}
FILLER = "The implementation is complete and the requirement is met as described. " * 5


@pytest.fixture(autouse=True)
async def clean(engine: AsyncEngine) -> None:
    await reset_data(engine)


@pytest.fixture
def settings() -> Settings:
    return worker(ANTHROPIC_API_KEY=KEY, CLAUDE_MODEL_DEFAULT="claude-haiku-5-5")


def leaks(rendered: str, *, allow: tuple[str, ...] = ()) -> list[str]:
    """The secrets (whole, or their middle part) found in ``rendered``."""
    return [
        name
        for name, secret in SECRETS.items()
        if name not in allow and (secret in rendered or secret[7:-4] in rendered)
    ]


def final_turn() -> Reply:
    output = {**OUTPUTS["ImplementOutput"], "summary": f"Done; the build used {AWS}."}
    return Reply(
        (
            TextBlock(FILLER + f"The configured key is {KEY} and nothing else." + FILLER),
            ToolUseBlock("toolu_submit", "submit_result", output),
        ),
        stop_reason="tool_use",
    )


SCRIPT = (
    tool_turn("repo.read_file", {"path": "README.md"}, "toolu_readme"),
    tool_turn("repo.read_file", {"path": "src/settings.py"}, "toolu_settings"),
    tool_turn("repo.read_file", {"path": ".env"}, "toolu_env"),
    tool_turn("code.write_file", {"path": "src/notes.txt", "content": f"key:\n{PEM}\n"}, "toolu_write"),
    final_turn(),
)


async def a_run(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
    tmp_path: Path,
    provider: Any,
) -> tuple[Any, RecordingBus, uuid.UUID, Any]:
    await record(session, settings, key=KEY)
    project = await make_project(session)
    task = await make_task(session, project)
    await session.commit()
    await hold_lease(session, task.id)
    workspace = await task_worktree(
        tmp_path,
        {
            "README.md": f"# Project\nMirror: {URL}\nBuild key: {AWS}\n",
            "src/settings.py": f'DEBUG = False\npassword = "{PASSWORD}"\n',
            ".env": f"ANTHROPIC_API_KEY={KEY}\nPRIVATE={PEM}\n",
        },
    )
    bus = RecordingBus()
    runtime = build_agent_runtime(
        sessions=sessions,
        settings=settings,
        registry=REGISTRY,
        pricing=pricing,
        audit=AuditWriter(Redactor(settings.secret_values())),
        bus=bus,
        worker_id=WORKER_ID,
        providers=FakeProviders(provider),
    )
    agent_input = ImplementInput(task_id=task.id, step=STEP, requirements=[], gap_feedback=f"token={TOKEN}")
    request = run_request(
        "B14", task_id=task.id, project_id=project.id, agent_input=agent_input, workspace=workspace
    )
    return runtime, bus, task.id, request


async def rows_as_text(session: AsyncSession, query: str, task_id: uuid.UUID) -> str:
    found = (await session.execute(text(query), {"task_id": task_id})).all()
    return json.dumps([list(row) for row in found], default=str)


def request_parts(req: LLMRequest) -> tuple[str, str]:
    """(everything but the model's own earlier output, the model's own earlier output)."""
    own: list[Any] = []
    rest: list[Any] = [req.system, [(t.name, t.description, t.input_schema) for t in req.tools]]
    for message in req.messages:
        blocks = message.content if isinstance(message.content, tuple) else (TextBlock(message.content),)
        for block in blocks:
            if isinstance(block, TextBlock):
                (own if message.role == "assistant" else rest).append(block.text)
            elif isinstance(block, ToolUseBlock):
                own.append([block.name, block.input])
            elif isinstance(block, ToolResultBlock):
                rest.append(block.content)
    return json.dumps(rest, ensure_ascii=False), json.dumps(own, ensure_ascii=False)


async def test_an_agent_run_leaks_no_secret(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
    tmp_path: Path,
) -> None:
    provider = scripted(*SCRIPT, delta_size=16)
    runtime, bus, task_id, request = await a_run(session, sessions, settings, pricing, tmp_path, provider)
    with capture_logs() as logs:
        result = await runtime.run(request)
    assert result.status == "succeeded", result.error

    assert provider.requests  # syarat 3.1a C3/D3: the provider was really asked
    for req in provider.requests:
        rest, own = request_parts(req)
        assert leaks(rest) == [], req.metadata
        assert leaks(own, allow=("pem",)) == []  # only what the scripted model itself wrote (Temuan #12)

    runs = await rows_as_text(
        session, "SELECT input, output, error FROM agent_runs WHERE task_id = :task_id", task_id
    )
    assert leaks(runs) == []
    calls = await rows_as_text(
        session, "SELECT sanitized_input, output_summary FROM tool_calls WHERE task_id = :task_id", task_id
    )
    assert leaks(calls) == []
    usage = await rows_as_text(
        session, "SELECT request_id, model, provider FROM usage_records WHERE task_id = :task_id", task_id
    )
    assert leaks(usage) == []
    events = await rows_as_text(
        session, "SELECT type, payload FROM task_events WHERE task_id = :task_id", task_id
    )
    assert leaks(events) == []
    audits = await rows_as_text(
        session,
        "SELECT action, metadata FROM audit_events"
        " WHERE metadata->>'task_id' = CAST(CAST(:task_id AS uuid) AS text)"
        " OR resource_id IN (SELECT CAST(id AS text) FROM tool_calls WHERE task_id = CAST(:task_id AS uuid))",
        task_id,
    )
    assert leaks(audits) == []
    assert leaks(json.dumps(bus.messages, ensure_ascii=False)) == []
    # capture_logs runs no processor: this proves the secrets were never logged, not only redacted.
    assert leaks(json.dumps(logs, default=str, ensure_ascii=False)) == []


async def test_the_run_reached_every_path(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
    tmp_path: Path,
) -> None:
    """Guards the test above against passing empty."""
    provider = scripted(*SCRIPT, delta_size=16)
    runtime, bus, task_id, request = await a_run(session, sessions, settings, pricing, tmp_path, provider)
    result = await runtime.run(request)
    assert bus.of_type("agent.delta")
    statuses = {
        (c.tool_name, c.status)
        for c in await session.scalars(select(ToolCall).where(ToolCall.task_id == task_id))
    }
    assert ("repo.read_file", "ok") in statuses and ("code.write_file", "ok") in statuses
    assert ("repo.read_file", "denied") in statuses  # .env (LOW-2)
    stored = await session.get(AgentRun, result.agent_run_id)
    assert stored is not None and stored.output is not None and "[REDACTED]" in stored.output["summary"]
    assert len(provider.requests) >= 5
    readme = str(provider.requests[1].messages[-1].content)
    assert "Mirror: https://[REDACTED]@git.example.test/x" in readme  # the file did reach the model, redacted
    assert "token=[REDACTED]" in str(provider.requests[0].messages[0].content)


async def test_a_secret_in_a_failed_run_error_is_redacted(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    pricing: PricingTable,
    tmp_path: Path,
) -> None:
    class Broken(ScriptedProvider):
        async def create_message(self, req: LLMRequest, **kwargs: Any) -> Any:
            raise RuntimeError(f"upstream said: invalid x-api-key {KEY}")

    runtime, _, _, request = await a_run(session, sessions, settings, pricing, tmp_path, Broken())
    with capture_logs() as logs, pytest.raises(RuntimeError):
        await runtime.run(request)
    stored = (await session.scalars(select(AgentRun).where(AgentRun.task_id == request.task_id))).one()
    assert (stored.status, stored.error_code) == ("failed", "internal_error")
    assert stored.error is not None and "RuntimeError" in stored.error and "[REDACTED]" in stored.error
    assert leaks(stored.error) == []
    assert leaks(json.dumps(logs, default=str)) == []
    errors = [log for log in logs if log["event"] == "agent_run_internal_error"]
    assert [log["error_type"] for log in errors] == ["RuntimeError"]
