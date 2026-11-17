"""``POST /projects/{id}/tasks`` over HTTP with real PostgreSQL (API_CONTRACTS §5; spec WP-3.2 §8, §12).

A task is created ``draft`` behind the workflow-engine gate; no job is enqueued yet (WP-3.2b)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents import readiness
from app.core.db import make_sessionmaker
from app.models.audit import AuditEvent
from app.models.tasks import Task
from app.services.project_budget import BUDGET_MAX_TOKENS
from app.services.tasks import PROMPT_MAX, TITLE_MAX
from app.workflow.budget import BudgetFields, dump_budget
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    create,
    grant,
    headers,
    make_user,
    owner,
    owner_id,
    signed_in,
    workspace_root,
)

PASSWORD = "user-long-passphrase"  # make_user's


@pytest.fixture
def open_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The workflow engine arrives in WP-3.2b; until then the gate is closed (Lead decision #7)."""
    monkeypatch.setattr(readiness, "RUNTIMES_AVAILABLE", {"agent_runtime": False, "workflow_engine": True})


async def project(client: httpx.AsyncClient, name: str = "Tugas") -> str:
    response = await create(client, name)
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


async def post_task(
    client: httpx.AsyncClient, project_id: str, *, key: str | None = None, **body: object
) -> httpx.Response:
    payload = {"prompt": "Tambah fitur login\nDengan email", **body}
    sent = headers(client)
    if key is not None:
        sent["Idempotency-Key"] = key
    return await client.post(f"{PROJECTS}/{project_id}/tasks", json=payload, headers=sent)


async def task_rows(engine: AsyncEngine) -> list[Task]:
    async with make_sessionmaker(engine)() as session:
        return list(await session.scalars(select(Task)))


async def sql(engine: AsyncEngine, statement: str, **params: object) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(statement), params)


async def test_create_returns_202_with_a_draft_task(owner: httpx.AsyncClient, open_gate: None) -> None:  # noqa: F811
    project_id = await project(owner)
    response = await post_task(owner, project_id)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "accepted"
    data = body["data"]
    assert data["status"] == "draft" and data["priority"] == "normal" and data["workflow_type"] == "modify"
    assert data["project_id"] == project_id
    assert data["title"] == "Tambah fitur login"
    assert data["prompt"] == "Tambah fitur login\nDengan email"
    assert (data["iteration"], data["iteration_max"]) == (1, 3)
    assert data["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
        "cost_status": "estimated",
    }
    assert data["plan"] is None and data["clarification_questions"] == []
    assert data["pending_approval"] is None and data["latest_acceptance"] is None
    assert data["last_event_seq"] == 0 and data["error"] is None and data["cancel_requested"] is False
    assert data["created_by"]["display_name"] == "Tira"
    assert data["context_refs"] == []


async def test_checkpoint_user_set_is_empty_when_nothing_was_given(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    await post_task(owner, project_id)
    (task,) = await task_rows(engine)
    assert task.checkpoint == {"user_set": []}


async def test_gate_closed_is_503_and_writes_nothing(owner: httpx.AsyncClient, engine: AsyncEngine) -> None:  # noqa: F811
    project_id = await project(owner)
    response = await post_task(owner, project_id)
    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["details"] == [{"reason": "workflow_engine_unavailable"}]
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tasks"))).scalar_one() == 0


async def test_user_values_are_kept_and_marked(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    response = await post_task(owner, project_id, title="Judul saya", workflow_type="debug", priority="high")
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert (data["title"], data["workflow_type"], data["priority"]) == ("Judul saya", "debug", "high")
    (task,) = await task_rows(engine)
    assert task.checkpoint == {"user_set": ["title", "workflow_type"]}


async def test_prompt_and_title_are_redacted(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    secret = "sk-ant-" + "api03-" + "x" * 40
    response = await post_task(owner, project_id, prompt=f"{secret} perbaiki\nlalu uji")
    assert response.status_code == 202, response.text
    (task,) = await task_rows(engine)
    assert secret not in task.prompt and secret not in task.title
    assert "perbaiki" in task.prompt and task.prompt.endswith("lalu uji")
    assert secret not in response.text


async def test_effective_budget_is_copied(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    await sql(
        engine,
        "UPDATE projects SET settings = jsonb_set(settings, '{budget}', CAST(:b AS jsonb)) WHERE id = :id",
        b='{"max_tokens": 1000}',
        id=uuid.UUID(project_id),
    )
    response = await post_task(owner, project_id, budget={"max_cost_usd": "0.5"})
    assert response.status_code == 202, response.text
    expected = dump_budget(
        BudgetFields(
            max_cost_usd=Decimal("0.5"), max_tokens=1000, max_tool_iterations=None, max_duration_minutes=60
        )
    )
    (task,) = await task_rows(engine)
    assert task.budget_limit == expected
    assert response.json()["data"]["budget_limit"] == expected


@pytest.mark.parametrize(
    "budget",
    [
        {"max_tokens": BUDGET_MAX_TOKENS + 1},
        {"max_gold": 1},
        {"max_cost_usd": 0.5},
    ],
)
async def test_budget_over_the_bound_is_422(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    budget: dict[str, object],
) -> None:
    project_id = await project(owner)
    response = await post_task(owner, project_id, budget=budget)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"][0]["loc"][:2] == ["body", "budget"]


async def test_broken_project_budget_is_409(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    await sql(
        engine,
        "UPDATE projects SET settings = jsonb_set(settings, '{budget}', CAST(:b AS jsonb)) WHERE id = :id",
        b='{"max_tokens": -5}',
        id=uuid.UUID(project_id),
    )
    response = await post_task(owner, project_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == [{"reason": "project_budget_invalid"}]
    assert await task_rows(engine) == []


@pytest.mark.parametrize(
    ("status", "reason"),
    [("archived", "project_archived"), ("deleting_pending_approval", "project_not_active")],
)
async def test_inactive_project_refuses_tasks(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
    status: str,
    reason: str,
) -> None:
    project_id = await project(owner)
    await sql(engine, "UPDATE projects SET status = :s WHERE id = :id", s=status, id=uuid.UUID(project_id))
    response = await post_task(owner, project_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "CONFLICT"
    assert response.json()["error"]["details"] == [{"reason": reason}]
    assert await task_rows(engine) == []


async def test_repository_not_ready_is_409(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    await sql(
        engine,
        "UPDATE repositories SET sync_state = 'importing' WHERE project_id = :id",
        id=uuid.UUID(project_id),
    )
    response = await post_task(owner, project_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == [{"reason": "repository_not_ready"}]


async def test_hidden_characters_are_refused(owner: httpx.AsyncClient, open_gate: None) -> None:  # noqa: F811
    project_id = await project(owner)
    for bad in ("a\x00b", "a‮b"):
        assert (await post_task(owner, project_id, prompt=bad)).status_code == 422
        assert (await post_task(owner, project_id, title=bad)).status_code == 422
    assert (await post_task(owner, project_id, title="satu\ndua")).status_code == 422
    assert (await post_task(owner, project_id, prompt="a\n\tb")).status_code == 202


async def test_input_limits(owner: httpx.AsyncClient, open_gate: None) -> None:  # noqa: F811
    project_id = await project(owner)
    assert (await post_task(owner, project_id, prompt="   ")).status_code == 422
    assert (await post_task(owner, project_id, prompt="x" * (PROMPT_MAX + 1))).status_code == 422
    assert (await post_task(owner, project_id, prompt="x" * PROMPT_MAX)).status_code == 202
    assert (await post_task(owner, project_id, title="t" * (TITLE_MAX + 1))).status_code == 422
    twenty_one = [{"type": "file", "path": f"f{n}.txt"} for n in range(21)]
    assert (await post_task(owner, project_id, context_refs=twenty_one)).status_code == 422
    assert (await post_task(owner, project_id, workflow_type="deploy")).status_code == 422
    assert (await post_task(owner, project_id, priority="urgent")).status_code == 422
    assert (await post_task(owner, project_id, surprise=1)).status_code == 422


async def test_context_refs_are_stored_normalised(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    ref = {"type": "file", "path": "README.md"}
    response = await post_task(owner, project_id, context_refs=[ref, ref])
    assert response.status_code == 202, response.text
    (task,) = await task_rows(engine)
    assert task.context_refs == [ref]
    assert response.json()["data"]["context_refs"] == [ref]
    bad = await post_task(owner, project_id, context_refs=[{"type": "file", "path": ".env"}])
    assert bad.status_code == 422
    assert len(await task_rows(engine)) == 1


async def test_out_of_scope_is_404_and_reader_is_403(
    app,  # type: ignore[no-untyped-def]  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    await make_user(engine, "eve@example.test")
    eve = await signed_in(app, "eve@example.test", PASSWORD)
    reader_id = await make_user(engine, "rina@example.test", ("readonly", f"project:{project_id}"))
    assert reader_id
    rina = await signed_in(app, "rina@example.test", PASSWORD)
    try:
        assert (await post_task(eve, project_id)).status_code == 404
        assert (await post_task(rina, project_id)).status_code == 403
    finally:
        await eve.aclose()
        await rina.aclose()
    assert await task_rows(engine) == []


async def test_idempotent_replay_returns_the_first_task(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    key = str(uuid.uuid4())
    first = await post_task(owner, project_id, key=key)
    again = await post_task(owner, project_id, key=key)
    assert first.status_code == again.status_code == 202
    assert first.json()["data"]["id"] == again.json()["data"]["id"]
    assert len(await task_rows(engine)) == 1


async def test_created_is_audited(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    response = await post_task(owner, project_id)
    task_id = response.json()["data"]["id"]
    async with make_sessionmaker(engine)() as session:
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "task.created", AuditEvent.resource_id == task_id)
        )
    assert event is not None
    assert event.correlation_id == f"task:{task_id}"
    assert event.resource_type == "task" and event.resource_id == task_id
    assert str(event.project_id) == project_id and event.result == "success"
    assert event.metadata_["source"] == "api" and event.metadata_["workflow_type"] == "modify"


@pytest.mark.parametrize(
    ("sent", "stored"), [("a\r\nb", "a\nb"), ("a\rb", "a\nb"), ("  a\r\n\r\nb \n", "a\n\nb")]
)
async def test_crlf_is_normalised_and_the_prompt_is_stripped(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
    sent: str,
    stored: str,
) -> None:
    project_id = await project(owner)
    response = await post_task(owner, project_id, prompt=sent)
    assert response.status_code == 202, response.text
    (task,) = await task_rows(engine)
    assert task.prompt == stored
    assert response.json()["data"]["prompt"] == stored


async def test_the_prompt_limit_is_measured_after_strip(owner: httpx.AsyncClient, open_gate: None) -> None:  # noqa: F811
    project_id = await project(owner)
    assert (await post_task(owner, project_id, prompt="x" * PROMPT_MAX + "\r\n")).status_code == 202
    assert (await post_task(owner, project_id, prompt="x" * (PROMPT_MAX + 1))).status_code == 422
    assert (await post_task(owner, project_id, prompt="\x00" + "\r\n")).status_code == 422
    assert (await post_task(owner, project_id, prompt="a\r‮b")).status_code == 422
