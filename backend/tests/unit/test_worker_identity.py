"""worker_id() is the lease owner: one value per process, never shared across forked workers."""

from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys

import pytest

from app.worker import identity
from app.worker.identity import worker_id

MAX_LENGTH = 120


def _child(queue: multiprocessing.Queue) -> None:  # type: ignore[type-arg]
    queue.put((os.getpid(), worker_id()))


def _run_in(method: str) -> tuple[int, str]:
    ctx = multiprocessing.get_context(method)
    queue = ctx.Queue()
    proc = ctx.Process(target=_child, args=(queue,))
    proc.start()
    result = queue.get(timeout=30)
    proc.join(timeout=30)
    return result


def test_stable_within_one_process_and_contains_pid() -> None:
    assert worker_id() == worker_id()
    assert f":{os.getpid()}:" in worker_id()
    assert 1 <= len(worker_id()) <= MAX_LENGTH


# The fork is the point (Celery prefork forks after import); pytest's own threads trigger the 3.12 notice.
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_forked_child_gets_its_own_id_even_with_warm_parent_cache() -> None:
    parent = worker_id()  # warm the cache before the fork
    child_pid, child_id = _run_in("fork")
    assert child_id != parent
    assert f":{child_pid}:" in child_id


def test_spawned_child_gets_its_own_id() -> None:
    parent = worker_id()
    child_pid, child_id = _run_in("spawn")
    assert child_id != parent
    assert f":{child_pid}:" in child_id


def test_subprocess_gets_its_own_id() -> None:
    code = "from app.worker.identity import worker_id; print(worker_id())"
    out = subprocess.run(  # noqa: S603 - fixed argv, no input
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() != worker_id()


def test_long_hostname_is_truncated_but_pid_and_token_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity.socket, "gethostname", lambda: "h" * 500)
    monkeypatch.setattr(identity, "_cached", None)
    value = worker_id()
    assert len(value) <= MAX_LENGTH
    host, pid, token = value.rsplit(":", 2)
    assert pid == str(os.getpid())
    assert len(token) == 8
    assert set(host) == {"h"}
