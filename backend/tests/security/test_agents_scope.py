"""``GET /agents/{id}``: runs and stats only from projects the caller can see (AC-01, API §1 scope rule).

The catalog itself is the same for every signed-in user; the runs inside it are not.
"""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.test_agents_api import AGENTS, add_run, synced  # noqa: F401 - registry synced per test
from tests.integration.test_dashboard_api import add_project, add_task
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    app,
    clean_state,
    grant,
    make_user,
    owner_id,
    signed_in,
    workspace_root,
)

PASSWORD = "user-long-passphrase"  # make_user's


async def test_runs_and_stats_only_from_visible_projects(app: FastAPI, engine: AsyncEngine) -> None:  # noqa: F811
    alice = await make_user(engine, "alice@example.test")
    await make_user(engine, "bob@example.test")  # signed in, but sees no project
    carl = await make_user(engine, "carl@example.test")
    alices = await add_project(engine, alice, "Alice Project")
    shared = await add_project(engine, alice, "Shared Project")
    await grant(engine, carl, "readonly", f"project:{shared}")
    private_run = await add_run(
        engine, await add_task(engine, alices, alice, "running", "Private"), status="succeeded"
    )
    shared_run = await add_run(
        engine, await add_task(engine, shared, alice, "running", "Shared"), status="failed", duration_ms=500
    )

    async def seen(email: str) -> dict[str, object]:
        client = await signed_in(app, email, PASSWORD)
        try:
            response = await client.get(f"{AGENTS}/A02")
            assert response.status_code == 200, response.text
            return response.json()["data"]
        finally:
            await client.aclose()

    as_alice = await seen("alice@example.test")
    assert {run["id"] for run in as_alice["recent_runs"]} == {str(private_run), str(shared_run)}  # type: ignore[union-attr,index]
    assert as_alice["stats"]["runs"] == 2  # type: ignore[index]

    as_bob = await seen("bob@example.test")  # sees no project: no run, no count
    assert as_bob["recent_runs"] == []
    assert as_bob["stats"] == {"runs": 0, "success_rate": None, "avg_duration_ms": None}

    gone = await add_project(engine, alice, "Deleted Project", status="deleted")
    await add_run(engine, await add_task(engine, gone, alice, "running", "Gone"), status="succeeded")
    assert (await seen("alice@example.test"))["stats"]["runs"] == 2  # type: ignore[index]  # a deleted project counts for no one

    as_carl = await seen("carl@example.test")  # a grant on the shared project only
    assert [run["id"] for run in as_carl["recent_runs"]] == [str(shared_run)]  # type: ignore[union-attr,index]
    assert as_carl["stats"] == {"runs": 1, "success_rate": 0.0, "avg_duration_ms": 500}
    assert str(alices) not in str(as_carl)
