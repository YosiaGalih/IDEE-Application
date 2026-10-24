"""HttpSandboxClient against a mocked sandbox manager (respx; API_CONTRACTS §10)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from typing import Any

import httpx
import pytest
import respx

from app.sandbox.client import (
    FakeSandboxClient,
    HttpSandboxClient,
    SandboxAuthError,
    SandboxClient,
    SandboxRequestError,
    SandboxRunRequest,
    SandboxRunResult,
    SandboxUnavailable,
)

BASE = "http://sandbox-manager.test:8100"
TOKEN = "s" * 40
PROJECT = uuid.UUID("aaaaaaaa-1111-4111-8111-bbbbbbbbbbbb")
TASK = uuid.UUID("cccccccc-2222-4222-8222-dddddddddddd")
REQUEST = SandboxRunRequest(
    project_id=PROJECT,
    task_id=TASK,
    workspace_subpath=f"{PROJECT}/tasks/{TASK}",
    profile_id="python.pytest",
    args=["tests/test_a.py"],
    timeout_s=60,
)
HEALTHY = {"status": "ok", "driver": "docker", "docker": "ok", "images": {"python": True}, "runtime": "runc"}
FINAL: dict[str, Any] = {
    "run_id": "r1",
    "status": "failed",
    "exit_code": 1,
    "started_at": "2026-10-24T00:00:00+00:00",
    "finished_at": "2026-10-24T00:00:02+00:00",
    "duration_ms": 2000,
    "image": "kodekantor/sandbox-python:1",
    "image_digest": "sha256:abc",
    "stdout_tail": "1 failed\n",
    "stderr_tail": "",
    "truncated": False,
    "log_incomplete": False,
    "artifacts": {"junit": "<testsuites/>"},
    "artifact_status": {"junit": "ok", "coverage": "missing"},
    "error_code": None,
}
STARTED = httpx.Response(200, json={"run_id": "r1", "status": "queued", "timeout_s": 60})


def logs_page(
    status: str, *, done: bool = False, chunks: list[dict[str, str]] | None = None, offset: int = 0
) -> httpx.Response:
    items = chunks or []
    body = {
        "offset": offset,
        "next_offset": offset + len(items),
        "chunks": items,
        "status": status,
        "done": done,
    }
    return httpx.Response(200, json=body)


def mock_kill() -> respx.Route:
    return respx.post(f"{BASE}/v1/runs/r1/kill").mock(
        return_value=httpx.Response(200, json={"status": "killed"})
    )


@pytest.fixture
async def client() -> Any:
    sandbox = HttpSandboxClient(BASE, TOKEN, poll_interval_s=0)
    yield sandbox
    await sandbox.aclose()


@respx.mock
async def test_run_follows_logs_until_done(client: HttpSandboxClient) -> None:
    submit = respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(
        side_effect=[
            logs_page("running", chunks=[{"stream": "stdout", "data": "a"}]),
            logs_page("running", offset=1),
            logs_page("failed", chunks=[{"stream": "stderr", "data": "b"}], offset=1, done=True),
        ]
    )
    respx.get(f"{BASE}/v1/runs/r1").mock(return_value=httpx.Response(200, json=FINAL))
    seen: list[tuple[str, str]] = []

    async def on_log(stream: str, text: str) -> None:
        seen.append((stream, text))

    result = await client.run(REQUEST, on_log=on_log)
    assert seen == [("stdout", "a"), ("stderr", "b")]
    assert (result.status, result.exit_code, result.stdout) == ("failed", 1, "1 failed\n")
    assert result.artifacts == {"junit": "<testsuites/>"}
    assert result.artifact_status == {"junit": "ok", "coverage": "missing"}
    assert result.image_digest == "sha256:abc"
    sent = submit.calls.last.request
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(sent.content) == {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": f"{PROJECT}/tasks/{TASK}",
        "profile_id": "python.pytest",
        "args": ["tests/test_a.py"],
        "timeout_s": 60,
        "network": "none",
        "read_only": False,
        "client_request_id": None,
        "wait": False,
    }
    offsets = [c.request.url.params["offset"] for c in respx.calls if c.request.url.path.endswith("/logs")]
    assert offsets == ["0", "1", "1"]


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_errors(client: HttpSandboxClient, status: int) -> None:
    respx.post(f"{BASE}/v1/runs").mock(return_value=httpx.Response(status))
    with pytest.raises(SandboxAuthError) as info:
        await client.run(REQUEST)
    assert TOKEN not in str(info.value)


@respx.mock
async def test_request_errors_carry_the_manager_code(client: HttpSandboxClient) -> None:
    body = {"error": {"code": "INVALID_ARGS", "message": "An argument is not allowed by the profile"}}
    respx.post(f"{BASE}/v1/runs").mock(return_value=httpx.Response(422, json=body))
    with pytest.raises(SandboxRequestError) as info:
        await client.run(REQUEST)
    assert info.value.code == "INVALID_ARGS"


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(502, text="bad gateway"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"status": "queued"}),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_unavailable(client: HttpSandboxClient, response: Any) -> None:
    route = respx.post(f"{BASE}/v1/runs")
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    with pytest.raises(SandboxUnavailable) as info:
        await client.run(REQUEST)
    assert TOKEN not in str(info.value)


@respx.mock
async def test_run_lost_after_restart(client: HttpSandboxClient) -> None:
    respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(return_value=httpx.Response(404))
    kill = respx.post(f"{BASE}/v1/runs/r1/kill").mock(return_value=httpx.Response(404))
    with pytest.raises(SandboxUnavailable):
        await client.run(REQUEST)
    assert kill.called  # best effort: a run is never left behind


@respx.mock
@pytest.mark.parametrize(
    "record",
    [
        {**FINAL, "status": "running"},
        {**FINAL, "status": "passed"},
        {**FINAL, "status": "succeeded", "exit_code": 1},
        {**FINAL, "status": "succeeded", "exit_code": None},
        {**FINAL, "status": "failed", "exit_code": 0},
        {**FINAL, "status": "failed", "exit_code": None},
        {**FINAL, "run_id": "other"},
    ],
)
async def test_incoherent_records_are_not_trusted(client: HttpSandboxClient, record: dict[str, Any]) -> None:
    respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(return_value=logs_page("failed", done=True))
    respx.get(f"{BASE}/v1/runs/r1").mock(return_value=httpx.Response(200, json=record))
    mock_kill()
    with pytest.raises(SandboxUnavailable):
        await client.run(REQUEST)


@respx.mock
async def test_error_results_carry_the_manager_code(client: HttpSandboxClient) -> None:
    respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(return_value=logs_page("error", done=True))
    record = {
        **FINAL,
        "status": "error",
        "exit_code": 0,
        "error_code": "ARTIFACT_MISSING",
        "artifact_status": {"junit": "missing"},
        "log_incomplete": True,
    }
    respx.get(f"{BASE}/v1/runs/r1").mock(return_value=httpx.Response(200, json=record))
    result = await client.run(REQUEST)
    assert (result.status, result.error_code) == ("error", "ARTIFACT_MISSING")
    assert result.artifact_status == {"junit": "missing"} and result.log_incomplete is True


@respx.mock
async def test_stuck_run_is_killed_at_the_deadline(
    client: HttpSandboxClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.sandbox.client as client_module

    monkeypatch.setattr(client_module, "DEADLINE_SLACK_S", -60)  # deadline = the moment it runs
    respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(return_value=logs_page("running"))
    kill = mock_kill()
    with pytest.raises(SandboxUnavailable, match="did not finish"):
        await client.run(REQUEST)
    assert kill.called


@respx.mock
async def test_queue_time_is_not_run_time(client: HttpSandboxClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run waiting for a slot is not a stuck manager (backend review HIGH, silent-failure F9)."""
    import app.sandbox.client as client_module

    monkeypatch.setattr(client_module, "DEADLINE_SLACK_S", -60)
    respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(
        side_effect=[logs_page("queued")] * 5 + [logs_page("succeeded", done=True)]
    )
    succeeded = {**FINAL, "status": "succeeded", "exit_code": 0}
    respx.get(f"{BASE}/v1/runs/r1").mock(return_value=httpx.Response(200, json=succeeded))
    assert (await client.run(REQUEST)).status == "succeeded"


@respx.mock
async def test_queue_limit(client: HttpSandboxClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.sandbox.client as client_module

    monkeypatch.setattr(client_module, "QUEUE_LIMIT_S", -1)
    respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(return_value=logs_page("queued"))
    kill = mock_kill()
    with pytest.raises(SandboxUnavailable, match="free slot"):
        await client.run(REQUEST)
    assert kill.called


@respx.mock
async def test_cancelled_caller_kills_the_run() -> None:
    sandbox = HttpSandboxClient(BASE, TOKEN, poll_interval_s=0.01)
    respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(return_value=logs_page("running"))
    kill = mock_kill()
    task = asyncio.create_task(sandbox.run(REQUEST))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert kill.called
    await sandbox.aclose()


@respx.mock
async def test_failing_on_log_kills_the_run(client: HttpSandboxClient) -> None:
    respx.post(f"{BASE}/v1/runs").mock(return_value=STARTED)
    respx.get(f"{BASE}/v1/runs/r1/logs").mock(
        return_value=logs_page("running", chunks=[{"stream": "stdout", "data": "x"}])
    )
    kill = mock_kill()

    async def on_log(stream: str, text: str) -> None:
        raise RuntimeError("redis publish failed")

    with pytest.raises(RuntimeError, match="redis publish failed"):
        await client.run(REQUEST, on_log=on_log)
    assert kill.called


@respx.mock
async def test_retry_key_is_sent(client: HttpSandboxClient) -> None:
    submit = respx.post(f"{BASE}/v1/runs").mock(
        return_value=httpx.Response(422, json={"error": {"code": "X"}})
    )
    with pytest.raises(SandboxRequestError):
        await client.run(dataclasses.replace(REQUEST, client_request_id="step-7"))
    assert json.loads(submit.calls.last.request.content)["client_request_id"] == "step-7"


@respx.mock
async def test_kill_and_kill_task(client: HttpSandboxClient) -> None:
    kill = mock_kill()
    respx.post(f"{BASE}/v1/tasks/{TASK}/kill").mock(
        return_value=httpx.Response(200, json={"killed": 2, "failed": 0})
    )
    await client.kill("r1")
    assert kill.called
    assert await client.kill_task(TASK) == 2


@respx.mock
async def test_kill_task_reports_failures(client: HttpSandboxClient) -> None:
    respx.post(f"{BASE}/v1/tasks/{TASK}/kill").mock(
        return_value=httpx.Response(200, json={"killed": 1, "failed": 1})
    )
    with pytest.raises(SandboxUnavailable, match="could not be killed"):
        await client.kill_task(TASK)


@respx.mock
@pytest.mark.parametrize(
    ("response", "healthy"),
    [
        (httpx.Response(200, json=HEALTHY), True),
        (httpx.Response(200, json={**HEALTHY, "docker": "unavailable"}), False),
        (httpx.Response(200, json={**HEALTHY, "images": {"python": True, "node": False}}), False),
        (httpx.Response(200, json={**HEALTHY, "images": {}}), False),
        (httpx.Response(200, json={**HEALTHY, "driver": "fake"}), False),
        (httpx.Response(401), False),
        (httpx.Response(200, text="[]"), False),
        (httpx.ConnectError("refused"), False),
    ],
)
async def test_health(client: HttpSandboxClient, response: Any, healthy: bool) -> None:
    route = respx.get(f"{BASE}/healthz")
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    assert await client.health() is healthy


@respx.mock
async def test_fake_driver_allowed_outside_production() -> None:
    sandbox = HttpSandboxClient(BASE, TOKEN, allow_fake_driver=True)
    respx.get(f"{BASE}/healthz").mock(return_value=httpx.Response(200, json={**HEALTHY, "driver": "fake"}))
    assert await sandbox.health() is True
    await sandbox.aclose()


async def test_fake_client() -> None:
    succeeded = SandboxRunResult("f1", "succeeded", 0, "", "", False, 1, "fake/image:1", None, {})
    fake = FakeSandboxClient(results={"python.pytest": succeeded}, logs=[("stdout", "hi")])
    seen: list[str] = []

    async def on_log(stream: str, text: str) -> None:
        seen.append(text)

    result = await fake.run(REQUEST, on_log=on_log)
    assert result.status == "succeeded" and seen == ["hi"]
    assert fake.requests == [REQUEST]
    assert await fake.kill_task(TASK) == 0 and fake.killed_tasks == [TASK]


async def test_unprogrammed_fake_never_succeeds() -> None:
    result = await FakeSandboxClient().run(REQUEST)
    assert (result.status, result.error_code) == ("error", "FAKE_NOT_PROGRAMMED")


async def test_clients_satisfy_the_protocol() -> None:
    http = HttpSandboxClient(BASE, TOKEN)
    clients: list[SandboxClient] = [http, FakeSandboxClient()]  # checked by pyright
    assert len(clients) == 2
    await http.aclose()
