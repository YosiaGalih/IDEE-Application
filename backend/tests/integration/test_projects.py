"""Projects over HTTP with real PostgreSQL, Redis and git (AC-01, AC-02, FR-012-014, FR-017, FR-018, FR-022).

Workspaces live under a per-test ``WORKSPACE_ROOT``. The cross-project matrix is in
``tests/security/test_cross_project.py``.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.audit import AuditWriter
from app.core.config import load_settings
from app.core.db import make_sessionmaker
from app.core.redaction import Redactor
from app.core.security import hash_password
from app.gitops.adapter import GitAdapter
from app.gitops.errors import GitError
from app.main import create_app
from app.models.audit import AuditEvent
from app.models.identity import User, UserRole
from app.models.projects import Project, Repository
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
        events = (
            await session.scalars(select(AuditEvent).where(AuditEvent.action == "project.create"))
        ).all()
    assert [(e.resource_id, e.result, e.metadata_) for e in events] == [
        (data["id"], "success", {"source": "blank"})
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
    assert response.status_code == 409
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
    for bad in (
        {"settings": {"budget": 5}},
        {"name": ""},
        {"slug": "x"},
        {"settings": {"auto_remediation": "yes"}},
    ):
        refused = await owner.patch(url, json=bad, headers=headers(owner))
        assert refused.status_code == 422, bad
    async with make_sessionmaker(engine)() as session:
        updates = (
            await session.scalars(select(AuditEvent).where(AuditEvent.action == "project.update"))
        ).all()
    assert [sorted(e.metadata_["fields"]) for e in updates] == [
        ["description", "name", "settings"],
        ["settings"],
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
    from app.core.errors import AppError
    from app.core.rbac import RoleGrant
    from app.services import projects

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
