"""Run lifecycle (API_CONTRACTS §10): records, the concurrency cap, bounded logs, kill.

Runs live in memory. After a restart the worker's lease and recovery logic (WP-1.1f) decides what to rerun,
and the manager removes containers it left behind (``Driver.cleanup_orphans``).
"""

from __future__ import annotations

import asyncio
import codecs
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
TAIL_BYTES = 64 * 1024  # the end of each stream, kept even after the stored log is truncated
CHUNK_CHARS = 64 * 1024  # adjacent frames of one stream are merged up to this, until a reader sees them
LOG_PAGE_CHARS = 256 * 1024  # [Menebak] one /logs response
RETAIN_S = 3600  # [Menebak] a final run stays readable this long
MAX_RETAINED = 500  # [Menebak] and at most this many final runs are kept
WAIT_SLACK_S = 30  # wait=true returns at the latest this long after the run's own timeout
log = logging.getLogger(__name__)


class DriverError(Exception):
    """The driver could not act (e.g. Docker unreachable). Mapped to 503 by the API."""


class IdempotencyConflict(Exception):
    """A ``client_request_id`` was reused for a different run."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_tail(data: bytes) -> str:
    """Decode the kept tail; a character cut at the start of the window is dropped, not replaced."""
    start = 0
    while start < min(3, len(data)) and data[start] & 0xC0 == 0x80:
        start += 1
    return data[start:].decode("utf-8", "replace")


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
    container_name: str | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    finished_mono: float | None = None
    log_incomplete: bool = False  # the log stream broke or was not drained: stored output may be partial
    artifact_status: dict[str, str] = field(default_factory=dict)  # name → ok|missing|too_large|not_regular
    client_request_id: str | None = None
    _started: float = 0.0
    _delivered: int = 0  # chunks before this index were sent to a reader and never change again
    _tails: dict[str, bytearray] = field(
        default_factory=lambda: {"stdout": bytearray(), "stderr": bytearray()}
    )
    _decoders: dict[str, codecs.IncrementalDecoder] = field(default_factory=dict)

    def mark_running(self) -> None:
        self.status = "running"

    def mark_started(self) -> None:
        """The container (or the fake) has started: ``started_at``/``duration_ms`` measure the run itself."""
        self.started_at = _now()
        self._started = time.monotonic()

    def finish(
        self, status: RunStatus, *, exit_code: int | None = None, error_code: str | None = None
    ) -> None:
        if self.done.is_set():  # the first final status stands
            log.warning("run %s: second final status %s ignored", self.run_id, status)
            return
        self.status = status
        self.exit_code = exit_code
        self.error_code = error_code
        self.finished_at = _now()
        self.finished_mono = time.monotonic()
        if self._started:
            self.duration_ms = int((self.finished_mono - self._started) * 1000)
        self.done.set()

    def append(self, stream: str, data: bytes) -> None:
        """Store output up to ``max_output_bytes`` (then ``truncated``); each stream's tail is always kept."""
        tail = self._tails.setdefault(stream, bytearray())
        tail += data[-TAIL_BYTES:]
        del tail[:-TAIL_BYTES]
        if self.truncated:
            return
        room = self.max_output_bytes - self.size
        if len(data) > room:
            data = data[:room]
            self.truncated = True
        if not data:
            return
        self.size += len(data)
        decoder = self._decoders.setdefault(stream, codecs.getincrementaldecoder("utf-8")("replace"))
        text = decoder.decode(data, final=self.truncated)
        if not text:
            return
        last = len(self.chunks) - 1
        if (
            last >= self._delivered
            and self.chunks[last][0] == stream
            and len(self.chunks[last][1]) < CHUNK_CHARS
        ):
            self.chunks[last] = (stream, self.chunks[last][1] + text)
        else:
            self.chunks.append((stream, text))

    def logs_page(self, offset: int) -> dict[str, Any]:
        """Chunks from ``offset``, about ``LOG_PAGE_CHARS`` at most; ``done`` once every chunk was sent."""
        start = min(max(offset, 0), len(self.chunks))
        end, size = start, 0
        while end < len(self.chunks):
            size += len(self.chunks[end][1])
            if end > start and size > LOG_PAGE_CHARS:
                break
            end += 1
        self._delivered = max(self._delivered, end)
        return {
            "offset": start,
            "next_offset": end,
            "chunks": [{"stream": stream, "data": data} for stream, data in self.chunks[start:end]],
            "status": self.status,
            "done": self.done.is_set() and end == len(self.chunks),
        }

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
            "stdout_tail": _decode_tail(bytes(self._tails.get("stdout", b""))),
            "stderr_tail": _decode_tail(bytes(self._tails.get("stderr", b""))),
            "truncated": self.truncated,
            "log_incomplete": self.log_incomplete,
            "artifacts": dict(self.artifacts),
            "artifact_status": dict(self.artifact_status),
            "error_code": self.error_code,
        }


class Driver(Protocol):
    name: str

    async def execute(self, run: ValidatedRun, record: RunRecord) -> None:
        """Run to completion and fill ``record`` (output, artefacts, final status).

        Never raises for run errors. The registry has already marked the record ``running``; the driver calls
        ``mark_started`` when the container really starts.
        """
        ...

    async def kill(self, record: RunRecord) -> None:
        """Stop the run's container; raises ``DriverError`` when that is not possible."""
        ...

    async def health(self) -> str: ...

    async def images(self) -> dict[str, bool]: ...

    async def reap(self, active_run_ids: set[str]) -> int:
        """Remove the driver's containers that belong to no active run."""
        ...

    async def cleanup_orphans(self) -> int: ...


class RunRegistry:
    def __init__(self, driver: Driver, settings: Settings) -> None:
        self.driver = driver
        self.settings = settings
        self.runs: dict[str, RunRecord] = {}
        self._slots = asyncio.Semaphore(settings.max_concurrent_runs)
        self._tasks: set[asyncio.Task[None]] = set()

    async def submit(
        self, run: ValidatedRun, *, wait: bool, client_request_id: str | None = None
    ) -> RunRecord:
        """Start a run. A repeated ``client_request_id`` returns the run it already started (a retry)."""
        self._evict()
        if client_request_id is not None:
            existing = next((r for r in self.runs.values() if r.client_request_id == client_request_id), None)
            if existing is not None:
                if (existing.project_id, existing.profile_id) != (
                    str(run.project_id),
                    run.profile.profile_id,
                ):
                    raise IdempotencyConflict()
                return await self._wait(existing, run.timeout_s) if wait else existing
        record = RunRecord(
            run_id=secrets.token_hex(12),
            project_id=str(run.project_id),
            task_id=str(run.task_id) if run.task_id else None,
            profile_id=run.profile.profile_id,
            timeout_s=run.timeout_s,
            max_output_bytes=self.settings.max_output_bytes,
            client_request_id=client_request_id,
        )
        self.runs[record.run_id] = record
        task = asyncio.create_task(self._run(run, record))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await self._wait(record, run.timeout_s) if wait else record

    @staticmethod
    async def _wait(record: RunRecord, timeout_s: int) -> RunRecord:
        """Past the slack the caller gets the run as it stands and polls (never a 500)."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(record.done.wait(), timeout_s + WAIT_SLACK_S)
        return record

    def _evict(self) -> None:
        now = time.monotonic()
        final = sorted(
            (r for r in self.runs.values() if r.finished_mono is not None),
            key=lambda r: r.finished_mono or 0.0,
        )
        excess = max(len(final) - MAX_RETAINED, 0)
        for index, record in enumerate(final):
            if index < excess or now - (record.finished_mono or now) > RETAIN_S:
                del self.runs[record.run_id]

    async def _run(self, run: ValidatedRun, record: RunRecord) -> None:
        try:
            async with self._slots:
                if record.done.is_set():  # killed while queued
                    return
                record.mark_running()  # no await between the check and this: a kill sees "running" now
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
        """Queued: final at once. Running: the driver stops the container and the run ends ``killed``."""
        record.kill_requested = True
        if record.status == "queued":
            record.finish("killed")
        elif record.status == "running":
            await self.driver.kill(record)

    async def kill_many(self, records: list[RunRecord]) -> tuple[int, int]:
        """Kill each record; one failure never stops the others. Returns (killed, failed)."""
        killed = failed = 0
        for record in records:
            try:
                await self.kill(record)
                killed += 1
            except Exception:
                log.exception("run %s: kill failed", record.run_id)
                failed += 1
        return killed, failed

    async def kill_task(self, task_id: str) -> tuple[int, int]:
        return await self.kill_many(
            [r for r in self.runs.values() if r.task_id == task_id and r.status not in FINAL]
        )

    def active_run_ids(self) -> set[str]:
        return {r.run_id for r in self.runs.values() if r.status not in FINAL}

    async def shutdown(self) -> None:
        await self.kill_many([r for r in self.runs.values() if r.status not in FINAL])
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
