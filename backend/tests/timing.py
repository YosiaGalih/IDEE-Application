"""A wall-clock bound that a loaded test machine cannot break by chance (backend review M3, final review of
74bf8d4).

A linear-time test asserts that 64 KiB of pathological input is handled in under a second. Under a full
suite a run can stall (garbage collection, another process), so one slow run is no proof of a slow pattern:
``best_of`` runs the code again, up to three times, and keeps the fastest. A pattern that is truly slow is
slow every time, so the bound still catches it.
"""

from __future__ import annotations

import time
from collections.abc import Callable


def best_of[T](
    fn: Callable[[], T], *, under: float, runs: int = 3, clock: Callable[[], float] = time.perf_counter
) -> tuple[float, T]:
    """The fastest of up to ``runs`` runs of ``fn``, stopping at the first that takes less than ``under``
    seconds: its elapsed time and its result. The caller asserts the bound on the time."""
    best: tuple[float, T] | None = None
    for _ in range(max(runs, 1)):
        started = clock()
        result = fn()
        elapsed = clock() - started
        if best is None or elapsed < best[0]:
            best = (elapsed, result)
        if elapsed < under:
            break
    assert best is not None
    return best
