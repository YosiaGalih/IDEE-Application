"""Docker driver against a scripted Engine API (``httpx.MockTransport``; ADR-009, ADR-026 (2)(3), ADR-033)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from sandbox_manager.config import Settings
from sandbox_manager.docker_driver import DockerDriver, DockerEngine, prepare_private_dirs
from sandbox_manager.hostconfig import run_artifacts_dir
from sandbox_manager.profiles import Profile
from sandbox_manager.runs import RunRegistry
from sandbox_manager.validation import RunRequest, ValidatedRun, validate

from .conftest import PROJECT, TASK, repo_workspace, task_workspace

ALLOWED = [
    ("GET", r"/_ping"),
    ("GET", r"/version"),
    ("GET", r"/images/.+/json"),  # the reference is percent-encoded on the wire
    ("POST", r"/containers/create"),
    ("POST", r"/containers/[0-9a-f]+/(start|wait|kill)"),
    ("GET", r"/containers/[0-9a-f]+/(logs|json)"),
    ("DELETE", r"/containers/[0-9a-f]+"),
    ("GET", r"/containers/json"),
]
CID = "c0ffee"


def frame(stream: int, data: bytes) -> bytes:
    return bytes([stream, 0, 0, 0]) + len(data).to_bytes(4, "big") + data


@dataclass
class FakeEngine:
    """Just enough of the Engine API; ``on_start`` plays the container's part on the host."""

    image_present: bool = True
    exit_code: int = 0
    log: bytes = b""
    log_chunk: int = 3  # deliver logs in awkward pieces to exercise the demultiplexer
    run_forever: bool = False
    fail: dict[str, int] = field(default_factory=dict)  # path regex → status
    on_start: Callable[[dict[str, Any]], None] | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)
    spec: dict[str, Any] | None = None
    removed: list[str] = field(default_factory=list)
    killed: asyncio.Event = field(default_factory=asyncio.Event)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        for pattern, status in self.fail.items():
            if re.fullmatch(pattern, path):
                return httpx.Response(status, json={"message": "boom"})
        if path == "/_ping":
            return httpx.Response(200, text="OK")
        if path.startswith("/images/"):
            return httpx.Response(200 if self.image_present else 404, json={})
        if path == "/containers/create":
            self.spec = json.loads(request.content)
            return httpx.Response(201, json={"Id": CID})
        if path.endswith("/start"):
            if self.on_start is not None and self.spec is not None:
                self.on_start(self.spec)
            return httpx.Response(204)
        if path.endswith("/wait"):
            if self.run_forever:
                await self.killed.wait()
                return httpx.Response(200, json={"StatusCode": 137})
            return httpx.Response(200, json={"StatusCode": self.exit_code})
        if path.endswith("/logs"):
            return httpx.Response(200, content=self._log_stream())
        if path.endswith("/json") and path.startswith("/containers/") and path != "/containers/json":
            return httpx.Response(200, json={"Image": "sha256:abc"})
        if path.endswith("/kill"):
            self.killed.set()
            return httpx.Response(204)
        if method == "DELETE":
            self.removed.append(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        if path == "/containers/json":
            return httpx.Response(200, json=[{"Id": "0dd1"}, {"Id": "0dd2"}])
        return httpx.Response(500)

    async def _log_stream(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self.log), self.log_chunk):
            yield self.log[start : start + self.log_chunk]
        if self.run_forever:
            await self.killed.wait()


def make_driver(settings: Settings, fake: FakeEngine) -> DockerDriver:
    engine = DockerEngine("tcp://docker-socket-proxy:2375")
    engine.client = httpx.AsyncClient(
        base_url="http://docker-socket-proxy:2375", transport=httpx.MockTransport(fake.handle)
    )
    prepare_private_dirs(settings)
    return DockerDriver(settings, engine)


def a_run(settings: Settings, profiles: dict[str, Profile], **overrides: Any) -> ValidatedRun:
    body: dict[str, Any] = {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": f"{PROJECT}/tasks/{TASK}",
        "profile_id": "python.pytest",
    }
    body.update(overrides)
    return validate(RunRequest.model_validate(body), profiles, settings)


async def execute(settings: Settings, fake: FakeEngine, run: ValidatedRun) -> Any:
    driver = make_driver(settings, fake)
    registry = RunRegistry(driver, settings)
    return await registry.submit(run, wait=True)


def mount_source(spec: dict[str, Any], target: str) -> Path:
    return Path(next(m["Source"] for m in spec["HostConfig"]["Mounts"] if m["Target"] == target))


async def test_successful_run(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)

    def container(spec: dict[str, Any]) -> None:
        (mount_source(spec, "/workspace/.kk") / "junit.xml").write_text('<testsuite tests="3"/>')
        (mount_source(spec, "/workspace/.kk") / "coverage.xml").write_text("<coverage/>")

    fake = FakeEngine(log=frame(1, b"3 passed\n") + frame(2, b"warning\n"), on_start=container)
    record = await execute(settings, fake, a_run(settings, profiles))
    assert (record.status, record.exit_code, record.error_code) == ("succeeded", 0, None)
    assert record.image == "kodekantor/sandbox-python:1"
    assert record.image_digest == "sha256:abc"
    public = record.public()
    assert public["stdout_tail"] == "3 passed\n"
    assert public["stderr_tail"] == "warning\n"
    assert record.artifacts == {"junit": '<testsuite tests="3"/>', "coverage": "<coverage/>"}
    assert fake.removed == [CID]
    assert not run_artifacts_dir(settings, record.run_id).exists()


async def test_failed_run(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    fake = FakeEngine(exit_code=1, log=frame(1, b"1 failed\n"))
    record = await execute(settings, fake, a_run(settings, profiles))
    assert (record.status, record.exit_code) == ("failed", 1)
    assert fake.removed == [CID]


async def test_missing_image_never_pulls(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    fake = FakeEngine(image_present=False)
    record = await execute(settings, fake, a_run(settings, profiles))
    assert (record.status, record.error_code) == ("error", "IMAGE_MISSING")
    assert all(not path.startswith("/images/create") for _, path in fake.calls)
    assert ("POST", "/containers/create") not in fake.calls


async def test_only_allowed_endpoints(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    fake = FakeEngine(log=frame(1, b"ok\n"))
    driver = make_driver(settings, fake)
    await driver.cleanup_orphans()
    await driver.health()
    record = await RunRegistry(driver, settings).submit(a_run(settings, profiles), wait=True)
    assert record.status == "succeeded"
    assert fake.calls
    for method, path in fake.calls:
        assert any(m == method and re.fullmatch(p, path) for m, p in ALLOWED), (method, path)


async def test_log_demux_across_chunks(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    log = frame(1, b"out-1 ") + frame(2, b"err-1 ") + frame(1, b"") + frame(1, b"out-2")
    for chunk in (1, 5, 8, 9, len(log)):
        fake = FakeEngine(log=log, log_chunk=chunk)
        record = await execute(settings, fake, a_run(settings, profiles))
        assert record.public()["stdout_tail"] == "out-1 out-2", chunk
        assert record.public()["stderr_tail"] == "err-1 ", chunk


async def test_output_limit(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    fake = FakeEngine(log=frame(1, b"a" * 600) + frame(2, b"b" * 600), log_chunk=4096)
    record = await execute(replace(settings, max_output_bytes=1000), fake, a_run(settings, profiles))
    assert record.truncated is True
    assert record.size == 1000
    assert record.public()["stderr_tail"] == "b" * 400


async def test_timeout_kills_and_removes(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    fake = FakeEngine(run_forever=True, log=frame(1, b"started\n"))
    record = await execute(settings, fake, a_run(settings, profiles, timeout_s=1))
    assert (record.status, record.exit_code) == ("timeout", None)
    assert ("POST", f"/containers/{CID}/kill") in fake.calls
    assert fake.removed == [CID]
    assert record.public()["stdout_tail"] == "started\n"


async def test_kill_request(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    fake = FakeEngine(run_forever=True)
    registry = RunRegistry(make_driver(settings, fake), settings)
    record = await registry.submit(a_run(settings, profiles, timeout_s=60), wait=False)
    for _ in range(100):
        if record.container_id:
            break
        await asyncio.sleep(0.01)
    await registry.kill(record)
    await asyncio.wait_for(record.done.wait(), 5)
    assert (record.status, record.exit_code) == ("killed", 137)
    assert fake.removed == [CID]


@pytest.mark.parametrize(
    "failing", [r"/containers/create", r"/containers/\w+/start", r"/containers/\w+/wait"]
)
async def test_engine_errors_end_as_error_and_remove(
    settings: Settings, profiles: dict[str, Profile], failing: str
) -> None:
    task_workspace(settings)
    fake = FakeEngine(fail={failing: 500})
    record = await execute(settings, fake, a_run(settings, profiles))
    assert (record.status, record.error_code) == ("error", "DOCKER_ERROR")
    assert fake.removed == ([] if "create" in failing else [CID])
    assert list((settings.private_dir / "runs").iterdir()) == []


async def test_docker_unreachable(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    driver = make_driver(settings, FakeEngine())
    driver.engine.client = httpx.AsyncClient(base_url="http://proxy", transport=httpx.MockTransport(refuse))
    assert await driver.health() == "unavailable"
    record = await RunRegistry(driver, settings).submit(a_run(settings, profiles), wait=True)
    assert (record.status, record.error_code) == ("error", "DOCKER_UNAVAILABLE")


async def test_artifacts_are_not_followed_through_links(
    settings: Settings, profiles: dict[str, Profile], tmp_path: Path
) -> None:
    task_workspace(settings)
    secret = tmp_path / "host-secret"
    secret.write_text("host secret")

    def container(spec: dict[str, Any]) -> None:
        folder = mount_source(spec, "/workspace/.kk")
        os.symlink(secret, folder / "junit.xml")
        (folder / "coverage.xml").write_bytes(b"x" * (1024 * 1024 + 1))

    record = await execute(settings, FakeEngine(on_start=container), a_run(settings, profiles))
    assert record.status == "succeeded"
    assert record.artifacts == {}
    assert secret.read_text() == "host secret"


async def test_disk_growth_limit(settings: Settings, profiles: dict[str, Profile]) -> None:
    workspace = task_workspace(settings)

    def container(spec: dict[str, Any]) -> None:
        with open(mount_source(spec, "/workspace") / "big.bin", "wb") as handle:
            handle.truncate(2 * 1024 * 1024)

    limited = replace(settings, max_workspace_growth_mb=1)
    record = await execute(limited, FakeEngine(on_start=container), a_run(limited, profiles))
    assert (record.status, record.error_code) == ("error", "DISK_LIMIT")
    assert (workspace / "big.bin").exists()  # the manager reports; cleanup is the worker's call


async def test_read_only_run_skips_growth_check(settings: Settings, profiles: dict[str, Profile]) -> None:
    repo_workspace(settings)
    run = a_run(settings, profiles, task_id=None, workspace_subpath=f"{PROJECT}/repo")
    fake = FakeEngine()
    record = await execute(settings, fake, run)
    assert record.status == "succeeded"
    assert fake.spec is not None
    workspace_mount = next(m for m in fake.spec["HostConfig"]["Mounts"] if m["Target"] == "/workspace")
    assert workspace_mount["ReadOnly"] is True


async def test_kk_mountpoint(settings: Settings, profiles: dict[str, Profile], tmp_path: Path) -> None:
    workspace = task_workspace(settings)
    record = await execute(settings, FakeEngine(), a_run(settings, profiles))
    assert record.status == "succeeded"
    assert (workspace / ".kk").is_dir() and not (workspace / ".kk").is_symlink()
    os.rmdir(workspace / ".kk")
    os.symlink(tmp_path, workspace / ".kk")
    fake = FakeEngine()
    record = await execute(settings, fake, a_run(settings, profiles))
    assert (record.status, record.error_code) == ("error", "WORKSPACE_UNSAFE")
    assert ("POST", "/containers/create") not in fake.calls


async def test_cleanup_orphans(settings: Settings) -> None:
    fake = FakeEngine()
    removed = await make_driver(settings, fake).cleanup_orphans()
    assert removed == 2
    assert fake.removed == ["0dd1", "0dd2"]
    listing = next(call for call in fake.calls if call[1] == "/containers/json")
    assert listing == ("GET", "/containers/json")


async def test_kill_before_container_exists(settings: Settings, profiles: dict[str, Profile]) -> None:
    """A kill that arrives while the container is being created still stops it."""
    task_workspace(settings)
    fake = FakeEngine(run_forever=True)
    driver = make_driver(settings, fake)
    registry = RunRegistry(driver, settings)
    real_create = driver.engine.create

    async def slow_create(spec: dict[str, Any], name: str) -> str:
        await registry.kill(record)  # status is "running" but there is no container id yet
        return await real_create(spec, name)

    driver.engine.create = slow_create  # type: ignore[method-assign]
    record = await registry.submit(a_run(settings, profiles, timeout_s=60), wait=False)
    await asyncio.wait_for(record.done.wait(), 5)
    assert record.status == "killed"
    assert ("POST", f"/containers/{CID}/kill") in fake.calls
    assert fake.removed == [CID]


async def test_kill_before_create(settings: Settings, profiles: dict[str, Profile]) -> None:
    task_workspace(settings)
    fake = FakeEngine()
    driver = make_driver(settings, fake)
    registry = RunRegistry(driver, settings)
    real_exists = driver.engine.image_exists

    async def exists_then_kill(ref: str) -> bool:
        await registry.kill(record)
        return await real_exists(ref)

    driver.engine.image_exists = exists_then_kill  # type: ignore[method-assign]
    record = await registry.submit(a_run(settings, profiles), wait=False)
    await asyncio.wait_for(record.done.wait(), 5)
    assert record.status == "killed"
    assert ("POST", "/containers/create") not in fake.calls
