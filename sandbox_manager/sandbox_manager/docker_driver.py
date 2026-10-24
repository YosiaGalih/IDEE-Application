"""Docker driver: the Engine API over ``httpx`` (unix socket or the socket proxy; ADR-009).

**Endpoints used.** Only these, all allowed by the proxy flags `CONTAINERS`, `IMAGES`, `POST`, `PING`,
`VERSION` (ADR-026 (3)):
- `_ping`;
- `images/{ref}/json`;
- `containers/create|start|wait|logs|kill|json`, `DELETE containers/{id}`;
- `containers/json` (orphan sweep).

**Images.** An image is never pulled: a missing image ends the run with `IMAGE_MISSING` (ADR-026 (2)).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import stat
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from sandbox_manager.config import Settings
from sandbox_manager.hostconfig import SANDBOX_UID, build_container_spec, empty_file, run_artifacts_dir
from sandbox_manager.runs import RunRecord, RunStatus
from sandbox_manager.validation import RequestRejected, ValidatedRun

ARTIFACT_FILES = {"junit": "junit.xml", "coverage": "coverage.xml"}
MAX_ARTIFACT_BYTES = 1024 * 1024  # [Menebak]
_STREAMS = {1: "stdout", 2: "stderr"}
log = logging.getLogger(__name__)
Outcome = tuple[RunStatus, int | None, str | None]  # status, exit code, error code
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class DockerUnavailable(Exception):
    pass


class DockerEngine:
    """A minimal async Docker Engine API client."""

    def __init__(self, docker_host: str, *, timeout_s: float = 30.0) -> None:
        if docker_host.startswith("unix://"):
            transport = httpx.AsyncHTTPTransport(uds=docker_host.removeprefix("unix://"))
            base_url = "http://docker"
        elif docker_host.startswith("tcp://"):
            transport = httpx.AsyncHTTPTransport()
            base_url = "http://" + docker_host.removeprefix("tcp://")
        else:
            raise ValueError("DOCKER_HOST must be unix:// or tcp://")
        self.client = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=timeout_s)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise DockerUnavailable(type(exc).__name__) from exc

    async def ping(self) -> bool:
        try:
            return (await self._request("GET", "/_ping")).status_code == 200
        except DockerUnavailable:
            return False

    async def image_exists(self, ref: str) -> bool:
        response = await self._request("GET", f"/images/{quote(ref, safe='')}/json")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    async def create(self, spec: dict[str, Any], name: str) -> str:
        response = await self._request("POST", "/containers/create", params={"name": name}, json=spec)
        response.raise_for_status()
        return str(response.json()["Id"])

    async def start(self, container_id: str) -> None:
        (await self._request("POST", f"/containers/{container_id}/start")).raise_for_status()

    async def wait(self, container_id: str, timeout_s: float) -> int:
        response = await self._request(
            "POST", f"/containers/{container_id}/wait", timeout=httpx.Timeout(timeout_s, connect=10.0)
        )
        response.raise_for_status()
        return int(response.json()["StatusCode"])

    async def logs(self, container_id: str) -> AsyncIterator[tuple[str, bytes]]:
        """Demultiplexed stdout/stderr frames until the container stops."""
        params = {"follow": "1", "stdout": "1", "stderr": "1"}
        try:
            async with self.client.stream(
                "GET", f"/containers/{container_id}/logs", params=params, timeout=None
            ) as response:
                response.raise_for_status()
                buffer = b""
                async for chunk in response.aiter_bytes():
                    buffer += chunk
                    while len(buffer) >= 8:
                        size = int.from_bytes(buffer[4:8], "big")
                        if len(buffer) < 8 + size:
                            break
                        stream = _STREAMS.get(buffer[0], "stdout")
                        yield stream, buffer[8 : 8 + size]
                        buffer = buffer[8 + size :]
        except httpx.TransportError as exc:
            raise DockerUnavailable(type(exc).__name__) from exc

    async def image_id(self, container_id: str) -> str | None:
        response = await self._request("GET", f"/containers/{container_id}/json")
        if response.status_code != 200:
            return None
        return response.json().get("Image")

    async def kill(self, container_id: str) -> None:
        response = await self._request("POST", f"/containers/{container_id}/kill")
        if response.status_code not in (204, 404, 409):  # already gone or not running: fine
            response.raise_for_status()

    async def remove(self, container_id: str) -> None:
        response = await self._request(
            "DELETE", f"/containers/{container_id}", params={"force": "1", "v": "1"}
        )
        if response.status_code not in (204, 404):
            response.raise_for_status()

    async def managed_containers(self) -> list[str]:
        filters = json.dumps({"label": ["kk.managed=1"]})
        response = await self._request("GET", "/containers/json", params={"all": "1", "filters": filters})
        response.raise_for_status()
        return [str(item["Id"]) for item in response.json()]


def _workspace_bytes(root: Path) -> int:
    total = 0
    for folder, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            with contextlib.suppress(OSError):
                st = os.lstat(os.path.join(folder, name))
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
    return total


def _read_artifact(folder: Path, name: str) -> str | None:
    """A regular file directly in the run's artefact folder, never followed through a link, ≤ 1 MiB."""
    try:
        fd = os.open(folder / name, _READ_FLAGS)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_ARTIFACT_BYTES:
            return None
        return os.read(fd, MAX_ARTIFACT_BYTES + 1)[:MAX_ARTIFACT_BYTES].decode("utf-8", "replace")
    finally:
        os.close(fd)


def _prepare_artifacts(folder: Path) -> None:
    """A fresh folder the sandbox user can write to and the manager can read and delete.

    In compose the manager runs as the sandbox user (uid 10001, like api/worker), so 0700 is enough.
    """
    os.mkdir(folder, 0o700)  # fails if it exists: a run id is never reused
    uid, gid = (int(part) for part in SANDBOX_UID.split(":"))
    if os.geteuid() == 0:
        os.chown(folder, uid, gid)
    elif os.geteuid() != uid:
        os.chmod(folder, 0o777)  # noqa: S103 - [Menebak] only where the manager runs as another uid


def prepare_private_dirs(settings: Settings) -> None:
    settings.private_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    (settings.private_dir / "runs").mkdir(mode=0o755, exist_ok=True)
    empty = empty_file(settings)
    if not empty.exists():
        empty.write_bytes(b"")
    os.chmod(empty, 0o444)


def ensure_mountpoint(workspace: Path) -> None:
    """``<workspace>/.kk`` must exist as a real folder for the artefact mount (needed for read-only binds)."""
    target = workspace / ".kk"
    try:
        mode = os.lstat(target).st_mode
    except FileNotFoundError:
        os.mkdir(target, 0o755)  # never follows a link: mkdir fails if the name exists
        return
    if not stat.S_ISDIR(mode):
        raise RequestRejected("WORKSPACE_UNSAFE", "The workspace .kk is not a folder")


class DockerDriver:
    name = "docker"

    def __init__(self, settings: Settings, engine: DockerEngine | None = None) -> None:
        self.settings = settings
        self.engine = engine or DockerEngine(settings.docker_host)

    async def health(self) -> str:
        return "ok" if await self.engine.ping() else "unavailable"

    async def cleanup_orphans(self) -> int:
        removed = 0
        for container_id in await self.engine.managed_containers():
            await self.engine.remove(container_id)
            removed += 1
        return removed

    async def kill(self, record: RunRecord) -> None:
        if record.container_id:
            await self.engine.kill(record.container_id)

    async def execute(self, run: ValidatedRun, record: RunRecord) -> None:
        """The final status is written only after the container is removed and the artefacts are read.

        A caller that sees a final status can rely on nothing still writing into the workspace.
        """
        image = self.settings.images[run.profile.image]
        record.mark_running(image)
        artifacts = run_artifacts_dir(self.settings, record.run_id)
        outcome: Outcome = ("error", None, "MANAGER_ERROR")
        try:
            outcome = await self._run_container(run, record, image, artifacts)
        except RequestRejected as exc:  # the workspace changed between validation and the run
            outcome = ("error", None, exc.code)
        except DockerUnavailable:
            outcome = ("error", None, "DOCKER_UNAVAILABLE")
        except httpx.HTTPStatusError:
            outcome = ("error", None, "DOCKER_ERROR")
        finally:
            if record.container_id is not None:
                try:
                    await self.engine.remove(record.container_id)
                except (DockerUnavailable, httpx.HTTPError):
                    # Swept at the next start (cleanup_orphans). The run already has an error status
                    # whenever the container could still be running (kill or wait failed first).
                    log.error("run %s: container %s not removed", record.run_id, record.container_id)
            await asyncio.to_thread(shutil.rmtree, artifacts, True)
        status, exit_code, error_code = outcome
        record.finish(status, exit_code=exit_code, error_code=error_code)

    async def _run_container(
        self, run: ValidatedRun, record: RunRecord, image: str, artifacts: Path
    ) -> Outcome:
        if not await self.engine.image_exists(image):
            return ("error", None, "IMAGE_MISSING")
        await asyncio.to_thread(ensure_mountpoint, run.workspace)
        await asyncio.to_thread(_prepare_artifacts, artifacts)
        before = None if run.read_only else await asyncio.to_thread(_workspace_bytes, run.workspace)
        spec = build_container_spec(run, record.run_id, self.settings)
        if record.kill_requested:
            return ("killed", None, None)
        record.container_id = await self.engine.create(spec, f"kk-run-{record.run_id}")
        await self.engine.start(record.container_id)
        if record.kill_requested:  # asked while the container was being created: driver.kill found no id
            await self.engine.kill(record.container_id)
        status, exit_code = await self._supervise(record, record.container_id, run.timeout_s)
        record.image_digest = await self.engine.image_id(record.container_id)
        for key, name in ARTIFACT_FILES.items():
            text = await asyncio.to_thread(_read_artifact, artifacts, name)
            if text is not None:
                record.artifacts[key] = text
        if before is not None:
            grown = await asyncio.to_thread(_workspace_bytes, run.workspace) - before
            if grown > self.settings.max_workspace_growth_mb * 1024 * 1024:
                return ("error", exit_code, "DISK_LIMIT")
        return (status, exit_code, None)

    async def _supervise(
        self, record: RunRecord, container_id: str, timeout_s: int
    ) -> tuple[RunStatus, int | None]:
        async def pump() -> None:
            async for stream, data in self.engine.logs(container_id):
                record.append(stream, data)

        reader = asyncio.create_task(pump())
        try:
            exit_code = await asyncio.wait_for(self.engine.wait(container_id, timeout_s + 5), timeout_s)
        except TimeoutError:
            await self.engine.kill(container_id)
            with contextlib.suppress(TimeoutError):  # removal (force) follows either way
                await asyncio.wait_for(self.engine.wait(container_id, 30), 30)
            await _drain(reader)
            return "timeout", None
        await _drain(reader)
        if record.kill_requested:
            return "killed", exit_code
        return ("succeeded" if exit_code == 0 else "failed"), exit_code


async def _drain(reader: asyncio.Task[None]) -> None:
    try:
        await asyncio.wait_for(reader, 10)
    except (TimeoutError, DockerUnavailable):
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, DockerUnavailable):
            await reader
