"""Projects over HTTP with real PostgreSQL, Redis and git (AC-01, AC-02, FR-012-014, FR-017, FR-018, FR-022).

Workspaces live under a per-test ``WORKSPACE_ROOT``. The cross-project matrix is in
``tests/security/test_cross_project.py``.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.core.errors import AppError
from app.core.rbac import RoleGrant
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.gitops.adapter import GitAdapter
from app.gitops.errors import GitError
from app.main import create_app
from app.models.audit import AuditEvent
from app.models.enums import TASK_STATUSES
from app.models.identity import User, UserRole
from app.models.projects import Project, Repository
from app.services import projects
from app.services.users import create_owner
from tests.conftest import make_test_env, reset_data

OWNER = ("owner@example.test", "owner-long-passphrase")
PROJECTS = "/api/v1/projects"


@pytest.fixture(autouse=True)
async def clean_state(engine: AsyncEngine) -> AsyncIterator[None]:
    await reset_data(engine)
    redis = Redis.from_url(make_test_env()["REDIS_URL"])
    await redis.flushdb()
    yield
    await redis.flushdb()
    await redis.aclose()


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces"
    root.mkdir()
    return root


@pytest.fixture
async def app(workspace_root: Path) -> AsyncIterator[FastAPI]:
    app = create_app(load_settings(make_test_env(COOKIE_SECURE="false", WORKSPACE_ROOT=str(workspace_root))))
    yield app
    await app.state.dependencies.aclose()


@pytest.fixture
async def owner_id(engine: AsyncEngine) -> uuid.UUID:
    return await create_owner(
        make_sessionmaker(engine),
        AuditWriter(Redactor()),
        email=OWNER[0],
        display_name="Tira",
        password=OWNER[1],
    )


async def make_user(engine: AsyncEngine, email: str, *roles: tuple[str, str]) -> uuid.UUID:
    async with make_sessionmaker(engine)() as session:
        user = User(
            email=email, display_name=email.split("@")[0], password_hash=hash_password("user-long-passphrase")
        )
        session.add(user)
        await session.flush()
        for role, scope in roles:
            session.add(UserRole(user_id=user.id, role_name=role, scope=scope))
        await session.commit()
        return user.id


async def grant(engine: AsyncEngine, user_id: uuid.UUID, role: str, scope: str) -> None:
    async with make_sessionmaker(engine)() as session:
        session.add(UserRole(user_id=user_id, role_name=role, scope=scope))
        await session.commit()


async def signed_in(app: FastAPI, email: str, password: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 50000))
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password, "remember_me": False}
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
async def owner(app: FastAPI, owner_id: uuid.UUID) -> AsyncIterator[httpx.AsyncClient]:
    client = await signed_in(app, *OWNER)
    yield client
    await client.aclose()


def headers(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["kk_csrf"], "Idempotency-Key": str(uuid.uuid4())}


async def create(client: httpx.AsyncClient, name: str = "Kalkulator Saya", **body: object) -> httpx.Response:
    payload: dict[str, object] = {"name": name, "source": {"type": "blank"}, **body}
    return await client.post(PROJECTS, json=payload, headers=headers(client))


def entries(folder: Path) -> list[Path]:
    return list(folder.iterdir())


def git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - test helper, fixed argv
        ["git", "--git-dir", str(repo / ".git"), "--work-tree", str(repo), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


async def test_create_blank(owner: httpx.AsyncClient, workspace_root: Path, engine: AsyncEngine) -> None:
    response = await create(owner, description="Latihan")
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert response.headers["location"] == f"{PROJECTS}/{data['id']}"
    assert data["slug"] == "kalkulator-saya"
    assert data["status"] == "active"
    assert data["default_branch"] == "main"
    assert data["description"] == "Latihan"
    assert data["repository"] == {
        "provider": "local",
        "remote_url": None,
        "branch": "main",
        "sync_state": "ready",
        "last_indexed_at": None,
        "error": None,
    }
    assert {"project:read", "project:write", "project:delete", "workspace:write"} <= set(data["permissions"])

    repo = workspace_root / data["id"] / "repo"
    assert git(repo, "show", "HEAD:README.md") == "# Kalkulator Saya"
    assert git(repo, "branch", "--show-current") == "main"
    assert git(repo, "log", "--format=%s|%an|%ae") == "Initial commit|Tira|owner@example.test"
    assert data["stats"]["last_commit"] == git(repo, "rev-parse", "HEAD")

    async with make_sessionmaker(engine)() as session:
        # audit_events is append-only and never reset: always filter on this test's resource.
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "project.create", AuditEvent.resource_id == data["id"]
                )
            )
        ).all()
    assert [(e.result, e.metadata_, e.ip) for e in events] == [
        ("success", {"source": "blank"}, "203.0.113.10")
    ]


async def test_create_from_template_runs_tests(owner: httpx.AsyncClient, workspace_root: Path) -> None:
    response = await create(owner, "Calc", source={"type": "template", "template_id": "python-basic"})
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    repo = workspace_root / data["id"] / "repo"
    assert data["repository"]["provider"] == "template"
    assert "python" in data["stack"]["languages"]
    assert git(repo, "log", "--format=%s") == "Initial commit from template python-basic"
    assert git(repo, "status", "--porcelain") == ""
    # The skeleton runs as created (FR-014): its own tests pass without network.
    run = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0, run.stdout + run.stderr


async def test_slug_is_unique_per_owner_and_never_empty(owner: httpx.AsyncClient) -> None:
    first = (await create(owner, "Demo App")).json()["data"]
    second = (await create(owner, "demo  app!")).json()["data"]
    symbols = (await create(owner, "!!! ???")).json()["data"]
    assert (first["slug"], second["slug"], symbols["slug"]) == ("demo-app", "demo-app-2", "project")


async def test_concurrent_creates_with_one_name_get_distinct_slugs(owner: httpx.AsyncClient) -> None:
    responses = await asyncio.gather(*(create(owner, "Same Name") for _ in range(5)))
    assert [r.status_code for r in responses] == [201] * 5, [r.text for r in responses]
    slugs = sorted(r.json()["data"]["slug"] for r in responses)
    assert slugs == ["same-name", "same-name-2", "same-name-3", "same-name-4", "same-name-5"]


async def test_a_failed_git_step_leaves_no_row_and_no_folder(
    owner: httpx.AsyncClient, workspace_root: Path, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: object, **__: object) -> str | None:
        raise GitError("commit_failed")

    monkeypatch.setattr(GitAdapter, "commit_all", broken)
    response = await create(owner)
    # Infrastructure, not the request: a 5xx, which the idempotency store does not keep for a retry.
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "GIT_ERROR"
    assert entries(workspace_root) == []
    async with make_sessionmaker(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(Project)) == 0
        assert await session.scalar(select(func.count()).select_from(Repository)) == 0


@pytest.mark.parametrize(
    "body",
    [
        {"name": "   ", "source": {"type": "blank"}},
        {"name": "a\nb", "source": {"type": "blank"}},
        {"name": "x" * 121, "source": {"type": "blank"}},
        {"name": "Tira\u202eevil", "source": {"type": "blank"}},  # bidi override: displays as another name
        {"name": "a\u200bb", "source": {"type": "blank"}},  # zero width space
        {"name": "a\x85b", "source": {"type": "blank"}},  # C1 control
        {"name": "ok", "description": "d" * 2001, "source": {"type": "blank"}},
        {"name": "ok", "source": {"type": "template", "template_id": "no-such-template"}},
        {"name": "ok", "source": {"type": "template", "template_id": "../python-basic"}},
        {"name": "ok", "source": {"type": "blank"}, "default_branch": "-x"},
        {"name": "ok", "source": {"type": "svn"}},
        {"name": "ok", "source": {"type": "blank"}, "owner_id": str(uuid.uuid4())},
    ],
)
async def test_invalid_input_is_422_and_creates_nothing(
    owner: httpx.AsyncClient, workspace_root: Path, body: dict[str, object]
) -> None:
    response = await owner.post(PROJECTS, json=body, headers=headers(owner))
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert entries(workspace_root) == []


async def test_remote_import_is_refused_until_egress_is_approved(
    owner: httpx.AsyncClient, workspace_root: Path
) -> None:
    response = await create(owner, source={"type": "git_https", "url": "https://github.com/a/b.git"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "POLICY_DENIED"
    assert entries(workspace_root) == []


async def test_list_shows_only_projects_in_scope(
    app: FastAPI, owner: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    mine = (await create(owner, "Mine")).json()["data"]
    bea_id = await make_user(engine, "bea@example.test")
    admin_id = await make_user(engine, "adi@example.test", ("admin", "global"))
    del admin_id
    bea = await signed_in(app, "bea@example.test", "user-long-passphrase")
    adi = await signed_in(app, "adi@example.test", "user-long-passphrase")
    try:
        hers = (await create(bea, "Hers")).json()["data"]
        assert [p["id"] for p in (await owner.get(PROJECTS)).json()["data"]] == [mine["id"]]
        await grant(engine, bea_id, "readonly", f"project:{mine['id']}")
        listed = {p["id"]: p for p in (await bea.get(PROJECTS)).json()["data"]}
        assert set(listed) == {mine["id"], hers["id"]}
        assert "project:write" not in listed[mine["id"]]["permissions"]
        assert "project:write" in listed[hers["id"]]["permissions"]
        # A global role reaches only projects the user owns or holds a role in (core/rbac.py).
        assert (await adi.get(PROJECTS)).json()["data"] == []
    finally:
        await bea.aclose()
        await adi.aclose()


async def test_list_filters_by_status_and_text(owner: httpx.AsyncClient) -> None:
    alpha = (await create(owner, "Alpha Service")).json()["data"]
    beta = (await create(owner, "Beta 100%")).json()["data"]
    await owner.post(f"{PROJECTS}/{beta['id']}/archive", headers=headers(owner))
    assert [p["id"] for p in (await owner.get(PROJECTS)).json()["data"]] == [alpha["id"]]
    assert [p["id"] for p in (await owner.get(PROJECTS, params={"status": "archived"})).json()["data"]] == [
        beta["id"]
    ]
    every = (await owner.get(PROJECTS, params={"status": "all"})).json()["data"]
    assert {p["id"] for p in every} == {alpha["id"], beta["id"]}
    assert [p["id"] for p in (await owner.get(PROJECTS, params={"q": "SERV"})).json()["data"]] == [
        alpha["id"]
    ]
    found = (await owner.get(PROJECTS, params={"q": "100%", "status": "all"})).json()["data"]
    assert [p["id"] for p in found] == [beta["id"]]
    # LIKE wildcards in q are literal.
    assert (await owner.get(PROJECTS, params={"q": "%", "status": "active"})).json()["data"] == []
    assert (await owner.get(PROJECTS, params={"status": "deleted"})).status_code == 422


async def test_get_has_repository_stats_and_permissions(owner: httpx.AsyncClient) -> None:
    created = (await create(owner)).json()["data"]
    response = await owner.get(f"{PROJECTS}/{created['id']}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["stats"]["open_tasks"] == 0
    assert data["stats"]["files"] is None  # until the index exists (WP-2.1d)
    assert len(data["stats"]["last_commit"]) == 40
    assert data["owner_id"] == created["owner_id"]


async def test_patch_updates_metadata_but_never_the_slug(
    owner: httpx.AsyncClient, engine: AsyncEngine
) -> None:
    created = (await create(owner, "Old Name", description="first")).json()["data"]
    url = f"{PROJECTS}/{created['id']}"
    response = await owner.patch(
        url,
        json={"name": "New Name", "description": None, "settings": {"plan_approval_required": True}},
        headers=headers(owner),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["name"], data["slug"], data["description"]) == ("New Name", "old-name", None)
    assert data["settings"] == {"plan_approval_required": True}
    second = await owner.patch(url, json={"settings": {"auto_remediation": False}}, headers=headers(owner))
    assert second.json()["data"]["settings"] == {"plan_approval_required": True, "auto_remediation": False}
    # The same value again changes nothing, so it is not audited as a change.
    same = await owner.patch(url, json={"settings": {"auto_remediation": False}}, headers=headers(owner))
    assert same.status_code == 200
    for bad in (
        {"settings": {"budget": 5}},
        {"name": ""},
        {"slug": "x"},
        {"settings": {"auto_remediation": "yes"}},
        {"settings": None},
        {"settings": {"auto_remediation": None}},
        {"name": None},
    ):
        refused = await owner.patch(url, json=bad, headers=headers(owner))
        assert refused.status_code == 422, bad
    async with make_sessionmaker(engine)() as session:
        updates = (
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.action == "project.update", AuditEvent.resource_id == created["id"])
                .order_by(AuditEvent.id)
            )
        ).all()
    # Settings are booleans, never secret: the audit records the new values (an approval control turned
    # off must leave its value on record).
    assert [e.metadata_ for e in updates] == [
        {"fields": ["description", "name", "settings"], "settings": {"plan_approval_required": True}},
        {"fields": ["settings"], "settings": {"auto_remediation": False}},
    ]


async def test_archive_and_unarchive(owner: httpx.AsyncClient) -> None:
    created = (await create(owner)).json()["data"]
    url = f"{PROJECTS}/{created['id']}"
    archived = await owner.post(f"{url}/archive", headers=headers(owner))
    assert archived.status_code == 200
    assert archived.json()["data"]["status"] == "archived"
    again = await owner.post(f"{url}/archive", headers=headers(owner))
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INVALID_TRANSITION"
    restored = await owner.post(f"{url}/unarchive", headers=headers(owner))
    assert restored.json()["data"]["status"] == "active"
    assert (await owner.post(f"{url}/unarchive", headers=headers(owner))).status_code == 409


async def test_templates_and_stack(owner: httpx.AsyncClient) -> None:
    templates = (await owner.get(f"{PROJECTS}/templates")).json()["data"]
    basic = next(t for t in templates if t["id"] == "python-basic")
    assert set(basic) == {"id", "name", "description", "stack", "icon"}
    created = (
        await create(owner, "Calc", source={"type": "template", "template_id": "python-basic"})
    ).json()["data"]
    stack = (await owner.get(f"{PROJECTS}/{created['id']}/stack")).json()["data"]
    assert stack == created["stack"]
    assert set(stack) == {"languages", "frameworks", "package_managers", "test_profiles", "entrypoints"}


async def test_signed_out_is_401(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as anonymous:
        assert (await anonymous.get(PROJECTS)).status_code == 401
        assert (await anonymous.get(f"{PROJECTS}/templates")).status_code == 401


async def test_service_refuses_unknown_settings_even_without_the_route(
    app: FastAPI, owner: httpx.AsyncClient, owner_id: uuid.UUID, workspace_root: Path, engine: AsyncEngine
) -> None:
    """Workers and agents call the service directly: it checks settings itself, not only the route."""
    created = (await create(owner)).json()["data"]
    deps = app.state.dependencies
    env = projects.ProjectEnv(deps.sessions, AuditWriter(Redactor()), deps.git, workspace_root)
    actor = projects.Actor(owner_id, OWNER[0], "Tira", (RoleGrant("owner", "global"),))
    for settings in ({"budget": 5}, {"auto_remediation": "yes"}):
        with pytest.raises(AppError) as refused:
            await projects.update_project(
                env,
                actor,
                uuid.UUID(created["id"]),
                projects.ProjectChanges(fields=frozenset({"settings"}), settings=settings),
            )
        assert refused.value.code == "VALIDATION_ERROR"
    with pytest.raises(AppError) as unknown_field:
        await projects.update_project(
            env, actor, uuid.UUID(created["id"]), projects.ProjectChanges(fields=frozenset({"slug"}))
        )
    assert unknown_field.value.code == "VALIDATION_ERROR"


# --- review gaps: the all-or-nothing invariant (spec anti-goal) -------------------------------------------


def service_env(app: FastAPI, workspace_root: Path) -> projects.ProjectEnv:
    deps = app.state.dependencies
    return projects.ProjectEnv(deps.sessions, AuditWriter(Redactor()), deps.git, workspace_root)


def owner_actor(owner_id: uuid.UUID) -> projects.Actor:
    return projects.Actor(owner_id, OWNER[0], "Tira", (RoleGrant("owner", "global"),))


async def counts(engine: AsyncEngine) -> tuple[int, int]:
    async with make_sessionmaker(engine)() as session:
        return (
            await session.scalar(select(func.count()).select_from(Project)) or 0,
            await session.scalar(select(func.count()).select_from(Repository)) or 0,
        )


async def test_a_commit_that_applied_but_raised_keeps_the_folder(
    owner: httpx.AsyncClient, workspace_root: Path, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The COMMIT reached the server and the reply was lost: the row exists, so the folder must stay."""
    armed = False
    audit = projects._audit  # pyright: ignore[reportPrivateUsage]
    commit = AsyncSession.commit

    async def arming_audit(*args: object, **kwargs: object) -> None:
        nonlocal armed
        await audit(*args, **kwargs)  # type: ignore[arg-type]
        armed = True  # the next commit is the create's own

    async def lost_reply(self: AsyncSession) -> None:
        nonlocal armed
        await commit(self)
        if armed:
            armed = False
            raise ConnectionResetError("reply lost")

    monkeypatch.setattr(projects, "_audit", arming_audit)
    monkeypatch.setattr(AsyncSession, "commit", lost_reply)
    # An unhandled error: the test transport raises it where a server would answer 500.
    with pytest.raises(ConnectionResetError):
        await create(owner)
    assert await counts(engine) == (1, 1)
    [folder] = entries(workspace_root)
    assert (folder / "repo" / ".git").is_dir()


async def test_a_failing_session_close_still_removes_the_folder(
    owner: httpx.AsyncClient, workspace_root: Path, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connection dies with the create: the session's own cleanup raises, the folder still goes."""
    armed = False
    close = AsyncSession.close

    async def broken(*_: object, **__: object) -> str | None:
        nonlocal armed
        armed = True
        raise GitError("commit_failed")

    async def failing_close(self: AsyncSession) -> None:
        nonlocal armed
        await close(self)
        if armed:
            armed = False
            raise RuntimeError("connection is closed")

    monkeypatch.setattr(GitAdapter, "commit_all", broken)
    monkeypatch.setattr(AsyncSession, "close", failing_close)
    with pytest.raises(RuntimeError):
        await create(owner)
    assert entries(workspace_root) == []
    assert await counts(engine) == (0, 0)


async def test_a_cancelled_create_leaves_no_row_and_no_folder(
    app: FastAPI,
    owner_id: uuid.UUID,
    workspace_root: Path,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def hangs(*_: object, **__: object) -> str | None:
        started.set()
        await asyncio.Event().wait()
        return None

    monkeypatch.setattr(GitAdapter, "commit_all", hangs)
    task = asyncio.create_task(
        projects.create_project(
            service_env(app, workspace_root),
            owner_actor(owner_id),
            name="Cancelled",
            description=None,
            source=projects.BlankSource(),
            default_branch="main",
        )
    )
    await asyncio.wait_for(started.wait(), 10)
    assert len(entries(workspace_root)) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.gather(*projects._settling)  # pyright: ignore[reportPrivateUsage]
    assert entries(workspace_root) == []
    assert await counts(engine) == (0, 0)


# --- review gaps: reads, lists, limits ---------------------------------------------------------------------


async def test_open_tasks_counts_only_unfinished_tasks(
    owner: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    created = (await create(owner)).json()["data"]
    async with make_sessionmaker(engine)() as session:
        for status in sorted(TASK_STATUSES):
            task_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO tasks (id, project_id, title, prompt, workflow_type, created_by) "
                    "VALUES (:id, :project, 't', 'p', 'create', :user)"
                ),
                {"id": task_id, "project": created["id"], "user": owner_id},
            )
            # Test setup only: place the task in ``status`` past the writer backstop.
            await session.execute(text("SELECT set_config('kk.status_writer', 'transition', true)"))
            await session.execute(
                text("UPDATE tasks SET status = :s WHERE id = :id"), {"s": status, "id": task_id}
            )
            await session.execute(text("SELECT set_config('kk.status_writer', '', true)"))
        await session.commit()
    data = (await owner.get(f"{PROJECTS}/{created['id']}")).json()["data"]
    closed = {"completed", "failed", "cancelled", "rejected"}
    assert data["stats"]["open_tasks"] == len(set(TASK_STATUSES) - closed) == 9


async def test_a_missing_repository_is_reported_not_hidden(
    owner: httpx.AsyncClient, workspace_root: Path
) -> None:
    created = (await create(owner)).json()["data"]
    shutil.rmtree(workspace_root / created["id"])
    data = (await owner.get(f"{PROJECTS}/{created['id']}")).json()["data"]
    assert (data["repository"]["sync_state"], data["repository"]["error"]) == ("error", "repository_missing")
    assert data["stats"]["last_commit"] is None
    [listed] = (await owner.get(PROJECTS)).json()["data"]
    assert (listed["repository"]["sync_state"], listed["repository"]["error"]) == (
        "error",
        "repository_missing",
    )
    # An update still answers for the change it made.
    renamed = await owner.patch(
        f"{PROJECTS}/{created['id']}", json={"name": "Renamed"}, headers=headers(owner)
    )
    assert renamed.status_code == 200
    assert renamed.json()["data"]["repository"]["error"] == "repository_missing"


async def test_list_is_paginated(owner: httpx.AsyncClient) -> None:
    for name in ("One", "Two", "Three"):
        assert (await create(owner, name)).status_code == 201
    first = (await owner.get(PROJECTS, params={"page_size": 2})).json()
    assert len(first["data"]) == 2
    assert first["meta"] == {"page": 1, "page_size": 2, "total": 3}
    second = (await owner.get(PROJECTS, params={"page_size": 2, "page": 2})).json()
    assert [p["name"] for p in second["data"]] == ["One"]  # newest first
    assert second["meta"] == {"page": 2, "page_size": 2, "total": 3}
    assert (await owner.get(PROJECTS)).json()["meta"] == {"page": 1, "page_size": 50, "total": 3}
    for bad in ({"page_size": 201}, {"page_size": 0}, {"page": 0}):
        assert (await owner.get(PROJECTS, params=bad)).status_code == 422, bad


async def test_search_refuses_control_characters(owner: httpx.AsyncClient) -> None:
    response = await owner.get(PROJECTS, params={"q": "a\x00b"})
    assert response.status_code == 422  # never reaches PostgreSQL (a NUL there is a 500)
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_an_idempotent_retry_creates_one_project(owner: httpx.AsyncClient, engine: AsyncEngine) -> None:
    sent = headers(owner)
    body = {"name": "Once", "source": {"type": "blank"}}
    first = await owner.post(PROJECTS, json=body, headers=sent)
    again = await owner.post(PROJECTS, json=body, headers=sent)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json()["data"]["id"] == first.json()["data"]["id"]
    assert await counts(engine) == (1, 1)


async def test_creates_are_rate_limited_per_user(
    owner: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "CREATE_RATE_LIMIT", 2)
    assert [(await create(owner, f"P{i}")).status_code for i in range(2)] == [201, 201]
    refused = await create(owner, "P3")
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "RATE_LIMITED"
    assert int(refused.headers["retry-after"]) > 0


async def test_a_refused_remote_import_is_audited(
    owner: httpx.AsyncClient, owner_id: uuid.UUID, engine: AsyncEngine
) -> None:
    response = await create(owner, source={"type": "git_https", "url": "https://github.com/a/b.git"})
    assert response.status_code == 403
    async with make_sessionmaker(engine)() as session:
        events = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "project.create",
                    AuditEvent.actor_id == str(owner_id),
                    AuditEvent.result == "denied",
                )
            )
        ).all()
    assert [(e.metadata_, e.ip) for e in events] == [
        ({"source": "git_https", "reason": "remote_import_disabled"}, "203.0.113.10")
    ]


def test_author_is_what_git_accepts_and_a_reader_sees() -> None:
    user = uuid.uuid4()

    def author(email: str, name: str) -> tuple[str, str]:
        found = projects._author(projects.Actor(user, email, name, ()))  # pyright: ignore[reportPrivateUsage]
        return found.name, found.email

    assert author("tira@example.test", "Tira <x>") == ("Tira x", "tira@example.test")
    # git refuses a name made only of punctuation: fall back to the address, then to a fixed name.
    assert author("tira@example.test", "';") == ("tira", "tira@example.test")
    assert author("..@example.test", "';") == ("KodeKantor user", "..@example.test")
    assert author("tira@example.test", "Ti\u202era") == ("Tira", "tira@example.test")
