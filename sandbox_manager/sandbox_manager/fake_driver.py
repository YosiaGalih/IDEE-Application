"""Fake driver for tests (``SANDBOX_DRIVER=fake``; refused in production by ``config``).

Behaviour is chosen by the first extra argument: ``ok`` (default), ``fail``, ``sleep``, ``spam``.
"""

from __future__ import annotations

import asyncio

from sandbox_manager.runs import RunRecord
from sandbox_manager.validation import ValidatedRun


class FakeDriver:
    name = "fake"

    def __init__(self) -> None:
        self.executed: list[ValidatedRun] = []

    async def health(self) -> str:
        return "ok"

    async def images(self) -> dict[str, bool]:
        return {"python": True, "node": True}

    async def cleanup_orphans(self) -> int:
        return 0

    async def reap(self, active_run_ids: set[str]) -> int:
        return 0

    async def kill(self, record: RunRecord) -> None:
        record.kill_requested = True

    async def execute(self, run: ValidatedRun, record: RunRecord) -> None:
        self.executed.append(run)
        record.image = "fake/image:1"
        record.mark_started()
        mode = run.args[0] if run.args else "ok"
        if mode == "sleep":
            for _ in range(run.timeout_s * 10):
                if record.kill_requested:
                    record.finish("killed")
                    return
                await asyncio.sleep(0.1)
            record.finish("timeout")
            return
        if mode == "spam":
            for _ in range(1000):
                record.append("stdout", b"x" * 4096)
        record.append("stdout", f"ran {run.profile.profile_id}\n".encode())
        if mode == "fail":
            record.append("stderr", b"assertion failed\n")
            record.finish("failed", exit_code=1)
            return
        record.artifacts["junit"] = '<testsuite tests="1" failures="0"><testcase name="t"/></testsuite>'
        record.artifact_status["junit"] = "ok"
        record.finish("succeeded", exit_code=0)
