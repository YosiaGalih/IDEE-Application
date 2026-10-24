"""Run lifecycle (API_CONTRACTS §10): records, the concurrency cap, bounded logs, kill.

Runs live in memory. After a restart the worker's lease and recovery logic (WP-1.1f) decides what to rerun,
and the manager removes containers it left behind (``Driver.cleanup_orphans``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from sandbox_manager.config import Settings
from sandbox_manager.validation import ValidatedRun

RunStatus = Literal["queued", "running", "succeeded", "failed", "timeout", "killed", "error"]
FINAL: frozenset[str] = frozenset({"succeeded", "failed", "timeout", "killed", "error"})
TAIL_BYTES = 64 * 1024
log = logging.getLogger(__name__)
WAIT_SLACK_S = 30  # wait=true returns at the latest this long after the run's own timeout


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunRecord:
    run_id: str
    project_id: str
    task_id: str | None
    profile_id: str
    timeout_s: int
    max_output_bytes: int
    status: RunStatus = "queued"
    exit_code: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    image: str | None = None
    image_digest: str | None = None
    error_code: str | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    chunks: list[tuple[str, str]] = field(default_factory=list)
    size: int = 0
    truncated: bool = False
    kill_requested: bool = False
    container_id: str | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    _started: float = 0.0

    def mark_running(self, image: str) -> None:
        self.status = "running"
        self.image = image
        self.started_at = _now()
        self._started = time.monotonic()

    def finish(
        self, status: RunStatus, *, exit_code: int | None = None, error_code: str | None = None
    ) -> None:
        self.status = status
        self.exit_code = exit_code
        self.error_code = error_code
        self.finished_at = _now()
        if self._started:
            self.duration_ms = int((time.monotonic() - self._started) * 1000)
        self.done.set()

    def append(self, stream: str, data: bytes) -> None:
        """Keep output up to ``max_output_bytes``; everything after is dropped and ``truncated`` set."""
        if self.truncated:
            return
        room = self.max_output_bytes - self.size
        if len(data) > room:
            data = data[:room]
            self.truncated = True
        if data:
            self.size += len(data)
            self.chunks.append((stream, data.decode("utf-8", "replace")))

    def _tail(self, stream: str) -> str:
        text = "".join(data for name, data in self.chunks if name == stream)
        return text[-TAIL_BYTES:]

    def public(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "image": self.image,
            "image_digest": self.image_digest,
            "stdout_tail": self._tail("stdout"),
            "stderr_tail": self._tail("stderr"),
            "truncated": self.truncated,
            "artifacts": dict(self.artifacts),
            "error_code": self.error_code,
        }


class Driver(Protocol):
    name: str

    async def execute(self, run: ValidatedRun, record: RunRecord) -> None:
        """Run to completion and fill ``record`` (status, output, artefacts). Never raises for run errors."""
        ...

    async def kill(self, record: RunRecord) -> None: ...

    async def health(self) -> str: ...

    async def cleanup_orphans(self) -> int: ...


class RunRegistry:
    def __init__(self, driver: Driver, settings: Settings) -> None:
        self.driver = driver
        self.settings = settings
        self.runs: dict[str, RunRecord] = {}
        self._slots = asyncio.Semaphore(settings.max_concurrent_runs)
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(self, run: ValidatedRun, *, wait: bool) -> RunRecord:
        record = RunRecord(
            run_id=secrets.token_hex(12),
            project_id=str(run.project_id),
            task_id=str(run.task_id) if run.task_id else None,
            profile_id=run.profile.profile_id,
            timeout_s=run.timeout_s,
            max_output_bytes=self.settings.max_output_bytes,
        )
        self.runs[record.run_id] = record
        task = asyncio.create_task(self._run(run, record))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if wait:  # past the slack the caller gets the run as it stands and polls (never a 500)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(record.done.wait(), run.timeout_s + WAIT_SLACK_S)
        return record

    async def _run(self, run: ValidatedRun, record: RunRecord) -> None:
        try:
            async with self._slots:
                if record.kill_requested:
                    record.finish("killed")
                    return
                await self.driver.execute(run, record)
        except Exception:  # a driver bug must still end the run as an error, never leave it "running"
            log.exception("run %s: driver failed", record.run_id)
            record.finish("error", error_code="MANAGER_ERROR")
        finally:
            if not record.done.is_set():
                log.error("run %s: driver returned without a final status", record.run_id)
                record.finish("error", error_code="MANAGER_ERROR")

    def get(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    async def kill(self, record: RunRecord) -> None:
        record.kill_requested = True
        if record.status == "running":
            await self.driver.kill(record)

    async def kill_task(self, task_id: str) -> int:
        active = [r for r in self.runs.values() if r.task_id == task_id and r.status not in FINAL]
        for record in active:
            await self.kill(record)
        return len(active)

    async def shutdown(self) -> None:
        for record in list(self.runs.values()):
            if record.status not in FINAL:
                await self.kill(record)
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
