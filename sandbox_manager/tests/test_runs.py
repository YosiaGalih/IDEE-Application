"""Run registry: concurrency cap (ADR-010), driver failures never leave a run "running"."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from sandbox_manager.config import Settings
from sandbox_manager.profiles import Profile
from sandbox_manager.runs import TAIL_BYTES, DriverError, IdempotencyConflict, RunRecord, RunRegistry
from sandbox_manager.validation import RunRequest, ValidatedRun, validate

from .conftest import PROJECT, TASK, task_workspace


def a_run(settings: Settings, profiles: dict[str, Profile]) -> ValidatedRun:
    task_workspace(settings)
    body = {
        "project_id": str(PROJECT),
        "task_id": str(TASK),
        "workspace_subpath": f"{PROJECT}/tasks/{TASK}",
        "profile_id": "python.pytest",
    }
    return validate(RunRequest.model_validate(body), profiles, settings)


class GateDriver:
    name = "gate"

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.release = asyncio.Event()

    async def health(self) -> str:
        return "ok"

    async def cleanup_orphans(self) -> int:
        return 0

    async def kill(self, record: RunRecord) -> None:
        return None

    async def execute(self, run: ValidatedRun, record: RunRecord) -> None:
        record.mark_started()
        self.active += 1
        self.peak = max(self.peak, self.active)
        await self.release.wait()
        self.active -= 1
        record.finish("succeeded", exit_code=0)


class BrokenDriver(GateDriver):
    async def execute(self, run: ValidatedRun, record: RunRecord) -> None:
        record.mark_started()
        raise RuntimeError("driver bug")


class SilentDriver(GateDriver):
    async def execute(self, run: ValidatedRun, record: RunRecord) -> None:
        record.mark_started()  # returns without finishing the record


async def test_concurrency_cap(settings: Settings, profiles: dict[str, Profile]) -> None:
    driver = GateDriver()
    registry = RunRegistry(driver, replace(settings, max_concurrent_runs=2))
    run = a_run(settings, profiles)
    records = [await registry.submit(run, wait=False) for _ in range(5)]
    await asyncio.sleep(0.05)
    assert driver.peak == 2
    assert [r.status for r in records].count("queued") == 3
    driver.release.set()
    await asyncio.wait_for(asyncio.gather(*(r.done.wait() for r in records)), 2)
    assert driver.peak == 2
    assert {r.status for r in records} == {"succeeded"}


async def test_kill_while_queued_is_final_at_once(settings: Settings, profiles: dict[str, Profile]) -> None:
    driver = GateDriver()
    registry = RunRegistry(driver, replace(settings, max_concurrent_runs=1))
    run = a_run(settings, profiles)
    first = await registry.submit(run, wait=False)
    queued = await registry.submit(run, wait=False)
    await asyncio.sleep(0.02)
    assert queued.status == "queued"
    await registry.kill(queued)
    assert queued.status == "killed" and queued.done.is_set()  # without waiting for a free slot
    driver.release.set()
    await asyncio.wait_for(first.done.wait(), 2)
    await asyncio.sleep(0.02)
    assert first.status == "succeeded"
    assert queued.status == "killed" and driver.peak == 1  # the killed run never reached the driver


class FailingKillDriver(GateDriver):
    def __init__(self) -> None:
        super().__init__()
        self.kills = 0

    async def kill(self, record: RunRecord) -> None:
        self.kills += 1
        if self.kills == 1:
            raise DriverError("docker unreachable")
        self.release.set()


async def test_one_failed_kill_does_not_stop_the_others(
    settings: Settings, profiles: dict[str, Profile]
) -> None:
    driver = FailingKillDriver()
    registry = RunRegistry(driver, settings)
    run = a_run(settings, profiles)
    records = [await registry.submit(run, wait=False) for _ in range(2)]
    await asyncio.sleep(0.02)
    assert await registry.kill_task(str(TASK)) == (1, 1)
    assert driver.kills == 2
    await asyncio.wait_for(asyncio.gather(*(r.done.wait() for r in records)), 2)


async def test_finished_runs_are_evicted(
    settings: Settings, profiles: dict[str, Profile], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sandbox_manager.runs as runs_module

    monkeypatch.setattr(runs_module, "MAX_RETAINED", 2)
    driver = GateDriver()
    driver.release.set()
    registry = RunRegistry(driver, settings)
    run = a_run(settings, profiles)
    first = [await registry.submit(run, wait=True) for _ in range(3)]
    active = await registry.submit(run, wait=True)
    assert first[0].run_id not in registry.runs  # oldest final run dropped
    assert {first[1].run_id, first[2].run_id, active.run_id} <= set(registry.runs)
    monkeypatch.setattr(runs_module, "RETAIN_S", -1)
    await registry.submit(run, wait=False)
    assert active.run_id not in registry.runs


async def test_client_request_id_returns_the_same_run(
    settings: Settings, profiles: dict[str, Profile]
) -> None:
    driver = GateDriver()
    registry = RunRegistry(driver, settings)
    run = a_run(settings, profiles)
    first = await registry.submit(run, wait=False, client_request_id="step-1")
    again = await registry.submit(run, wait=False, client_request_id="step-1")
    other = await registry.submit(run, wait=False, client_request_id="step-2")
    assert again is first and other is not first
    node = replace(run, profile=profiles["node.npm_test"])
    with pytest.raises(IdempotencyConflict):
        await registry.submit(node, wait=False, client_request_id="step-1")
    driver.release.set()
    await registry.shutdown()


async def test_driver_exception_ends_run_as_error(settings: Settings, profiles: dict[str, Profile]) -> None:
    registry = RunRegistry(BrokenDriver(), settings)
    record = await registry.submit(a_run(settings, profiles), wait=False)
    await asyncio.wait_for(record.done.wait(), 2)
    assert (record.status, record.error_code) == ("error", "MANAGER_ERROR")
    await registry.shutdown()


async def test_driver_that_forgets_to_finish(settings: Settings, profiles: dict[str, Profile]) -> None:
    registry = RunRegistry(SilentDriver(), settings)
    record = await registry.submit(a_run(settings, profiles), wait=True)
    assert (record.status, record.error_code) == ("error", "MANAGER_ERROR")


async def test_shutdown_kills_active_runs(settings: Settings, profiles: dict[str, Profile]) -> None:
    driver = GateDriver()
    registry = RunRegistry(driver, settings)
    record = await registry.submit(a_run(settings, profiles), wait=False)
    await asyncio.sleep(0.02)
    killed: list[str] = []

    async def kill(rec: RunRecord) -> None:
        killed.append(rec.run_id)
        driver.release.set()

    driver.kill = kill  # type: ignore[method-assign]
    await registry.shutdown()
    assert killed == [record.run_id]


def test_output_cap_is_exact() -> None:
    record = RunRecord("r", "p", None, "x.y", 10, max_output_bytes=10)
    record.append("stdout", b"12345")
    record.append("stderr", b"6789AB")
    record.append("stdout", b"more")
    assert record.size == 10
    assert record.truncated is True
    assert record.chunks == [("stdout", "12345"), ("stderr", "6789A")]
    # the tails keep the end of each stream, past the stored log
    assert record.public()["stdout_tail"] == "12345more"
    assert record.public()["stderr_tail"] == "6789AB"


def test_tail_is_the_last_bytes() -> None:
    record = RunRecord("r", "p", None, "x.y", 10, max_output_bytes=100)
    record.append("stdout", b"a" * TAIL_BYTES)
    record.append("stdout", "é".encode() + b"end")
    tail = record.public()["stdout_tail"]
    assert len(tail.encode()) <= TAIL_BYTES and tail.endswith("éend")


def test_multibyte_character_split_across_frames() -> None:
    record = RunRecord("r", "p", None, "x.y", 10, max_output_bytes=100)
    data = "café ✓".encode()
    for i in range(len(data)):
        record.append("stdout", data[i : i + 1])
    assert "".join(text for _, text in record.chunks) == "café ✓"
    assert record.public()["stdout_tail"] == "café ✓"


def test_chunks_merge_only_until_delivered() -> None:
    record = RunRecord("r", "p", None, "x.y", 10, max_output_bytes=1000)
    record.append("stdout", b"a")
    record.append("stdout", b"b")
    assert record.chunks == [("stdout", "ab")]
    page = record.logs_page(0)
    assert page["next_offset"] == 1 and page["done"] is False
    record.append("stdout", b"c")  # the delivered chunk never changes
    assert record.chunks == [("stdout", "ab"), ("stdout", "c")]
    record.finish("succeeded", exit_code=0)
    assert record.logs_page(1)["done"] is True


def test_logs_are_paged(monkeypatch: pytest.MonkeyPatch) -> None:
    import sandbox_manager.runs as runs_module

    monkeypatch.setattr(runs_module, "LOG_PAGE_CHARS", 10)
    record = RunRecord("r", "p", None, "x.y", 10, max_output_bytes=1000)
    for stream in ("stdout", "stderr", "stdout", "stderr"):
        record.append(stream, b"x" * 6)
    record.finish("failed", exit_code=1)
    pages, offset = [], 0
    while True:
        page = record.logs_page(offset)
        pages.append(len(page["chunks"]))
        offset = page["next_offset"]
        if page["done"]:
            break
    assert pages == [1, 1, 1, 1]  # done only once every chunk was sent


def test_second_final_status_is_ignored() -> None:
    record = RunRecord("r", "p", None, "x.y", 10, max_output_bytes=10)
    record.finish("killed")
    record.finish("succeeded", exit_code=0)
    assert (record.status, record.exit_code) == ("killed", None)


@pytest.mark.parametrize("data", [b"\xff\xfe", "é".encode()[:1]])
def test_output_is_decoded_safely(data: bytes) -> None:
    record = RunRecord("r", "p", None, "x.y", 10, max_output_bytes=100)
    record.append("stdout", data)
    assert record.public()["stdout_tail"]


async def test_wait_past_slack_returns_the_run_as_it_stands(
    settings: Settings, profiles: dict[str, Profile], monkeypatch: pytest.MonkeyPatch
) -> None:
    import sandbox_manager.runs as runs_module

    monkeypatch.setattr(runs_module, "WAIT_SLACK_S", -59.95)  # the 60 s profile timeout becomes 50 ms
    driver = GateDriver()
    registry = RunRegistry(driver, settings)
    record = await registry.submit(a_run(settings, profiles), wait=True)
    assert record.status == "running"
    driver.release.set()
    await asyncio.wait_for(record.done.wait(), 2)
    assert record.status == "succeeded"
