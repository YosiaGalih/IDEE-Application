"""``POST /projects/{id}/tasks`` over HTTP with real PostgreSQL (API_CONTRACTS §5; spec WP-3.2 §8, §12).

A task is created ``draft`` behind the workflow-engine gate; no job is enqueued yet (WP-3.2b)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine
from structlog.testing import capture_logs

from app.agents import readiness
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.events import emit_task_event, event_message
from app.core.rbac import ROLE_PERMISSIONS
from app.core.redaction import Redactor
from app.models.approvals import Approval
from app.models.audit import AuditEvent
from app.models.tasks import Task, TaskEvent
from app.services import tasks as tasks_service
from app.services.project_budget import BUDGET_MAX_TOKENS
from app.services.tasks import PROMPT_MAX, TITLE_MAX, TaskView
from app.workflow.budget import BudgetFields, dump_budget
from app.workspace import fs
from app.workspace.fs import Workspace
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
from tests.integration.test_usage_tables import record

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


async def created_audits(engine: AsyncEngine, project_id: str) -> list[AuditEvent]:
    """This project's ``task.created`` rows (audit_events is append-only: never assume an empty table)."""
    async with make_sessionmaker(engine)() as session:
        return list(
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "task.created", AuditEvent.project_id == uuid.UUID(project_id)
                )
            )
        )


def spy_disk(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The ``Workspace`` calls a ref check could make, in order (each one touches the disk)."""
    touched: list[str] = []
    for name in ("probe", "read_file", "tree"):
        original = getattr(Workspace, name)

        def spy(
            self: Workspace, *args: Any, _name: str = name, _original: Any = original, **kwargs: Any
        ) -> Any:
            touched.append(_name)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Workspace, name, spy)
    return touched


def repo_of(root: Path, project_id: str) -> Path:
    return root / project_id / "repo"


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
    assert await created_audits(engine, project_id) == []


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
    with capture_logs() as logs:
        response = await post_task(owner, project_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"] == [{"reason": "project_budget_invalid"}]
    assert await task_rows(engine) == []
    # SF L2: a stored budget nobody can read is an operator problem, logged without the settings content.
    [entry] = [entry for entry in logs if entry["event"] == "project_budget_invalid"]
    assert (entry["log_level"], entry["project_id"]) == ("error", project_id)
    assert "max_tokens" not in str(entry)


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
    for bad in ("a\x00b", "a\u202eb"):
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
    # spec §12.1.2 step 6
    assert event.metadata_ == {"task_id": task_id, "source": "api", "workflow_type": "modify"}


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
    assert (await post_task(owner, project_id, prompt="a\r\u202eb")).status_code == 422


TASKS_OF = PROJECTS + "/{}/tasks"


async def make_tasks(client: httpx.AsyncClient, project_id: str, *prompts: str) -> list[str]:
    ids = []
    for prompt in prompts:
        response = await post_task(client, project_id, prompt=prompt)
        assert response.status_code == 202, response.text
        ids.append(response.json()["data"]["id"])
    return ids


async def test_list_filters_and_pages(owner: httpx.AsyncClient, open_gate: None, engine: AsyncEngine) -> None:  # noqa: F811
    project_id = await project(owner)
    a, b, c = await make_tasks(owner, project_id, "satu", "dua", "tiga")
    async with engine.begin() as conn:  # the status guard accepts only workflow.states.transition's marker
        await conn.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
        await conn.execute(text("UPDATE tasks SET status = 'cancelled' WHERE id = :id"), {"id": uuid.UUID(c)})
    url = TASKS_OF.format(project_id)
    body = (await owner.get(url)).json()
    assert body["meta"]["total"] == 3
    assert [t["id"] for t in body["data"]] == [c, b, a]  # updated_at desc (c was just updated)
    assert (await owner.get(url, params={"status": "draft"})).json()["meta"]["total"] == 2
    both = await owner.get(url, params={"status": "draft,cancelled"})
    assert both.status_code == 200 and both.json()["meta"]["total"] == 3
    bad = await owner.get(url, params={"status": "nope"})
    assert bad.status_code == 422
    assert bad.json()["error"]["details"][0]["loc"] == ["query", "status"]
    paged = (await owner.get(url, params={"page_size": 2})).json()
    assert len(paged["data"]) == 2 and paged["meta"]["total"] == 3
    page2 = (await owner.get(url, params={"page_size": 2, "page": 2})).json()
    assert len(page2["data"]) == 1
    row = body["data"][0]
    assert set(row) == {
        "id", "project_id", "title", "status", "status_reason", "current_step", "workflow_type", "priority",
        "iteration", "usage", "has_pending_approval", "created_at", "updated_at",
    }  # fmt: skip


async def test_list_q_is_literal(owner: httpx.AsyncClient, open_gate: None) -> None:  # noqa: F811
    project_id = await project(owner)
    await make_tasks(
        owner, project_id, "diskon 50% hari ini", "diskon 500 hari ini", "snake_case rule", "snakeXcase"
    )
    url = TASKS_OF.format(project_id)
    hit = (await owner.get(url, params={"q": "50%"})).json()
    assert [t["title"] for t in hit["data"]] == ["diskon 50% hari ini"]
    under = (await owner.get(url, params={"q": "e_c"})).json()
    assert [t["title"] for t in under["data"]] == ["snake_case rule"]
    assert (await owner.get(url, params={"q": "DISKON"})).json()["meta"]["total"] == 2


async def test_list_usage_and_pending_approval(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    a, b = await make_tasks(owner, project_id, "tugas a", "tugas b")
    pid, aid = uuid.UUID(project_id), uuid.UUID(a)
    async with make_sessionmaker(engine)() as session:
        session.add_all([record(pid, task_id=aid), record(pid, task_id=aid)])
        session.add(
            Approval(
                project_id=pid,
                task_id=aid,
                action_type="tool_r3",
                risk_level=3,
                summary="R3",
                payload={"tool": "x"},
                payload_hash="b" * 64,
                requested_by="system:tool_gateway",
            )
        )
        await session.commit()
    rows = {t["id"]: t for t in (await owner.get(TASKS_OF.format(project_id))).json()["data"]}
    assert rows[a]["usage"]["input_tokens"] == 20 and rows[a]["usage"]["output_tokens"] == 10
    assert rows[a]["has_pending_approval"] is True
    assert rows[b]["has_pending_approval"] is False
    assert rows[b]["usage"]["input_tokens"] == 0


async def test_list_out_of_scope_is_404(
    app,  # type: ignore[no-untyped-def]  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    await make_user(engine, "eve@example.test")
    eve = await signed_in(app, "eve@example.test", PASSWORD)
    try:
        assert (await eve.get(TASKS_OF.format(project_id))).status_code == 404
    finally:
        await eve.aclose()


async def test_detail_matches_create(owner: httpx.AsyncClient, open_gate: None) -> None:  # noqa: F811
    project_id = await project(owner)
    created = (await post_task(owner, project_id)).json()["data"]
    response = await owner.get(f"/api/v1/tasks/{created['id']}")
    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    for data in (created, detail):
        data.pop("updated_at")
    assert detail == created


async def test_detail_out_of_scope_is_404(
    app,  # type: ignore[no-untyped-def]  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    [task_id] = await make_tasks(owner, project_id, "rahasia")
    await make_user(engine, "eve@example.test")
    eve = await signed_in(app, "eve@example.test", PASSWORD)
    try:
        assert (await owner.get(f"/api/v1/tasks/{task_id}")).status_code == 200  # the route does answer
        hidden = await eve.get(f"/api/v1/tasks/{task_id}")
        random = await eve.get(f"/api/v1/tasks/{uuid.uuid4()}")
        assert hidden.status_code == 404 and random.status_code == 404
        assert {**hidden.json(), "request_id": None} == {**random.json(), "request_id": None}
        assert (await owner.get(f"/api/v1/tasks/{uuid.uuid4()}")).status_code == 404
    finally:
        await eve.aclose()


# --- final review wave (WP-3.2a F1, F4, F9, F13) ------------------------------------------------------------


async def test_a_failed_view_stores_nothing_and_the_retry_creates_one_task(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BE M1 / SF M1: the view is built before the commit. A failure there stores no task and no audit row;
    the 5xx releases the idempotency key, and the retry with the same key creates exactly one task."""
    project_id = await project(owner)
    real = tasks_service.task_view
    calls: list[int] = []

    async def fails_once(*args: Any, **kwargs: Any) -> TaskView:
        calls.append(1)
        if len(calls) == 1:
            raise AppError("INTERNAL_ERROR", "Internal error", http_status=500)
        return await real(*args, **kwargs)

    monkeypatch.setattr(tasks_service, "task_view", fails_once)
    key = str(uuid.uuid4())
    failed = await post_task(owner, project_id, key=key)
    assert failed.status_code == 500, failed.text
    assert await task_rows(engine) == []
    assert await created_audits(engine, project_id) == []
    retried = await post_task(owner, project_id, key=key)
    assert retried.status_code == 202, retried.text
    (task,) = await task_rows(engine)
    assert retried.json()["data"]["id"] == str(task.id)
    assert len(await created_audits(engine, project_id)) == 1


async def test_the_raw_prompt_cap_refuses_padding_the_service_would_strip(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
) -> None:
    """The schema caps the raw prompt at 2 x PROMPT_MAX against abuse; under it the cleaned text counts."""
    project_id = await project(owner)
    padded = "x" + " " * (2 * PROMPT_MAX - 1)
    assert (await post_task(owner, project_id, prompt=padded)).status_code == 202  # cleaned: "x"
    over = await post_task(owner, project_id, prompt=padded + " ")  # 40 001 characters
    assert over.status_code == 422, over.text
    assert over.json()["error"]["details"][0]["loc"] == ["body", "prompt"]


async def test_a_user_title_is_stored_redacted(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    secret = "sk-ant-" + "api03-" + "y" * 40
    response = await post_task(owner, project_id, title=f"Rotasi {secret}")
    assert response.status_code == 202, response.text
    (task,) = await task_rows(engine)
    assert secret not in task.title and task.title.startswith("Rotasi ")
    assert secret not in response.text


async def test_only_task_create_can_be_403(
    app,  # type: ignore[no-untyped-def]  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    """A role without ``task:create`` gets 403 on POST. List and detail have no 403 case: every project role
    holds ``task:read``, so a project the user can see is one whose tasks they can read (else 404)."""
    assert all("task:read" in permissions for permissions in ROLE_PERMISSIONS.values())
    project_id = await project(owner)
    [task_id] = await make_tasks(owner, project_id, "lihat saja")
    await make_user(engine, "rev@example.test", ("reviewer", f"project:{project_id}"))
    reviewer = await signed_in(app, "rev@example.test", PASSWORD)
    try:
        refused = await post_task(reviewer, project_id)
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "FORBIDDEN"
        assert (await reviewer.get(TASKS_OF.format(project_id))).status_code == 200
        assert (await reviewer.get(f"/api/v1/tasks/{task_id}")).status_code == 200
    finally:
        await reviewer.aclose()
    assert len(await task_rows(engine)) == 1


async def test_detail_shows_the_pending_approval_and_who_may_decide_it(
    app,  # type: ignore[no-untyped-def]  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    [task_id] = await make_tasks(owner, project_id, "butuh persetujuan")
    async with make_sessionmaker(engine)() as session:
        session.add(
            Approval(
                project_id=uuid.UUID(project_id),
                task_id=uuid.UUID(task_id),
                action_type="tool_r3",
                risk_level=3,
                summary="R3",
                payload={"tool": "x"},
                payload_hash="c" * 64,
                requested_by="system:tool_gateway",
            )
        )
        await session.commit()
    mine = (await owner.get(f"/api/v1/tasks/{task_id}")).json()["data"]["pending_approval"]
    assert mine["status"] == "pending" and mine["can_decide"] is True
    await make_user(engine, "rina@example.test", ("readonly", f"project:{project_id}"))
    reader = await signed_in(app, "rina@example.test", PASSWORD)
    try:
        theirs = (await reader.get(f"/api/v1/tasks/{task_id}")).json()["data"]["pending_approval"]
    finally:
        await reader.aclose()
    assert theirs["id"] == mine["id"] and theirs["can_decide"] is False


async def test_a_bad_budget_is_refused_before_any_ref_is_probed(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = await project(owner)
    touched = spy_disk(monkeypatch)
    response = await post_task(
        owner, project_id, budget={"max_gold": 1}, context_refs=[{"type": "file", "path": "README.md"}]
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["loc"][:2] == ["body", "budget"]
    assert touched == []


async def test_an_invalid_agentignore_hides_every_ref_and_is_logged(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    workspace_root: Path,  # noqa: F811
) -> None:
    """SF L3 / BE L1: an oversize ``.agentignore`` fails closed (every path hidden), and the operator learns
    why from a warning that names the project, not the file's content."""
    project_id = await project(owner)
    (repo_of(workspace_root, project_id) / ".agentignore").write_bytes(b"#" * (fs.AGENTIGNORE_LIMIT + 1))
    with capture_logs() as logs:
        response = await post_task(owner, project_id, context_refs=[{"type": "file", "path": "README.md"}])
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["reason"] == "context_ref_hidden"
    [entry] = [entry for entry in logs if entry["event"] == "context_refs_agentignore_invalid"]
    assert (entry["log_level"], entry["project_id"]) == ("warning", project_id)
    assert "##" not in str(entry)


# --- task event history (WP-3.2b Task 1) --------------------------------------------------------------------


async def add_events(engine: AsyncEngine, task_id: str, count: int) -> None:
    async with make_sessionmaker(engine)() as session:
        for i in range(count):
            await emit_task_event(
                session,
                None,
                uuid.UUID(task_id),
                "task.log",
                {"level": "info", "message": str(i)},
                redactor=Redactor(),
            )
        await session.commit()


async def test_history_pages_in_seq_order(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    [task_id] = await make_tasks(owner, project_id, "riwayat")
    await add_events(engine, task_id, 5)
    first = await owner.get(f"/api/v1/tasks/{task_id}/events/history", params={"after_seq": 0, "limit": 2})
    assert first.status_code == 200, first.text
    body = first.json()
    assert [m["seq"] for m in body["data"]] == [1, 2]
    assert body["meta"]["next_after_seq"] == 2
    async with make_sessionmaker(engine)() as session:
        row = (await session.scalars(select(TaskEvent).where(TaskEvent.seq == 1))).one()
        assert body["data"][0] == event_message(row.task_id, row.seq, row.type, row.created_at, row.payload)
    last = await owner.get(f"/api/v1/tasks/{task_id}/events/history", params={"after_seq": 4, "limit": 2})
    assert [m["seq"] for m in last.json()["data"]] == [5]
    assert last.json()["meta"]["next_after_seq"] is None
    empty = await owner.get(f"/api/v1/tasks/{task_id}/events/history")
    assert len(empty.json()["data"]) == 5 and empty.json()["meta"]["next_after_seq"] is None


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "after_seq=-1"])
async def test_history_validates_query(
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    query: str,
) -> None:
    project_id = await project(owner)
    [task_id] = await make_tasks(owner, project_id, "riwayat")
    assert (await owner.get(f"/api/v1/tasks/{task_id}/events/history?{query}")).status_code == 422


async def test_history_out_of_scope_is_404(
    app,  # type: ignore[no-untyped-def]  # noqa: F811
    owner: httpx.AsyncClient,  # noqa: F811
    open_gate: None,
    engine: AsyncEngine,
) -> None:
    project_id = await project(owner)
    [task_id] = await make_tasks(owner, project_id, "rahasia")
    await make_user(engine, "eve@example.test")
    eve = await signed_in(app, "eve@example.test", PASSWORD)
    try:
        assert (await owner.get(f"/api/v1/tasks/{task_id}/events/history")).status_code == 200
        hidden = await eve.get(f"/api/v1/tasks/{task_id}/events/history")
        random = await eve.get(f"/api/v1/tasks/{uuid.uuid4()}/events/history")
        assert hidden.status_code == 404 and random.status_code == 404
        assert {**hidden.json(), "request_id": None} == {**random.json(), "request_id": None}
    finally:
        await eve.aclose()
