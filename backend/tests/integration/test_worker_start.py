"""A real ``celery worker`` process and the pricing table (spec WP-3.1f Keputusan Lead #10; plan 3.1f1
Task 5).

The broker is deliberately unreachable (port 9): a worker that wrongly swallowed a pricing error would go on
to the broker and retry forever, which the time limit turns into a failure; it never becomes ready, so it
never runs the startup recovery sweep against the test database.
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import make_test_env

BACKEND = Path(__file__).resolve().parents[2]
UNREACHABLE_BROKER = "redis://127.0.0.1:9/0"
LIMIT_S = 30.0
COMMAND = [
    sys.executable,
    "-m",
    "celery",
    "-A",
    "app.worker.celery_app",
    "worker",
    "--pool=solo",
    "--loglevel=INFO",
]


def _env(pricing_path: Path) -> dict[str, str]:
    return {**os.environ, **make_test_env(PRICING_PATH=str(pricing_path), REDIS_URL=UNREACHABLE_BROKER)}


@pytest.mark.parametrize("case", ["missing", "broken YAML"])
def test_worker_process_refuses_to_start_with_a_broken_pricing_file(tmp_path: Path, case: str) -> None:
    path = tmp_path / "pricing.yaml"
    if case == "broken YAML":
        path.write_text("version: [unclosed", encoding="utf-8")
    try:
        done = subprocess.run(  # noqa: S603 - a fixed command line, the test's own interpreter
            COMMAND, cwd=BACKEND, env=_env(path), timeout=LIMIT_S, capture_output=True, text=True, check=False
        )
    except subprocess.TimeoutExpired:
        pytest.fail("the worker did not exit: it went on towards the broker with no pricing table")
    output = done.stdout + done.stderr
    assert done.returncode != 0
    assert "PricingError" in output
    assert "ready" not in output


def test_worker_process_with_a_good_pricing_file_gets_past_pricing() -> None:
    """A valid table never stops start-up: the worker loads it and goes on to the (unreachable) broker."""
    process = subprocess.Popen(  # noqa: S603 - a fixed command line, the test's own interpreter
        COMMAND,
        cwd=BACKEND,
        env=_env(BACKEND / "config" / "pricing.yaml"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert process.stdout is not None
    seen: list[str] = []
    deadline = time.monotonic() + LIMIT_S
    try:
        while time.monotonic() < deadline and process.poll() is None:
            readable, _, _ = select.select([process.stdout], [], [], 0.5)
            if readable:
                line = process.stdout.readline()
                seen.append(line)
                if "Cannot connect to" in line:  # past the bootsteps, now retrying the broker
                    break
        still_running = process.poll() is None
    finally:
        process.kill()
        rest, _ = process.communicate(timeout=10)
    output = "".join(seen) + (rest or "")
    assert still_running, output
    assert "PricingError" not in output
    assert "pricing_loaded" in output and "2026-11-05" in output
    assert "Cannot connect to" in output
