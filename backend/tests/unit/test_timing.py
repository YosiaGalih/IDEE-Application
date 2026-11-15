"""``tests/timing.py``: the wall-clock bound of the WP-3.1f2c timing tests (backend review M3, final review of
74bf8d4)."""

from __future__ import annotations

from tests.timing import best_of


class FakeClock:
    """Each run of ``fn`` lasts the next of ``durations`` seconds on this clock."""

    def __init__(self, *durations: float) -> None:
        self.now = 0.0
        self.durations = list(durations)
        self.runs = 0

    def __call__(self) -> float:
        return self.now

    def fn(self) -> int:
        self.now += self.durations[self.runs]
        self.runs += 1
        return self.runs


def test_one_run_under_the_bound_is_enough() -> None:
    clock = FakeClock(0.2, 5.0, 5.0)
    assert best_of(clock.fn, under=1.0, clock=clock) == (0.2, 1)
    assert clock.runs == 1


def test_a_slow_run_is_run_again_until_one_is_under_the_bound() -> None:
    clock = FakeClock(2.0, 0.5, 5.0)
    assert best_of(clock.fn, under=1.0, clock=clock) == (0.5, 2)
    assert clock.runs == 2


def test_a_run_slow_every_time_gives_the_fastest_of_three() -> None:
    clock = FakeClock(3.0, 2.0, 2.5, 0.1)
    assert best_of(clock.fn, under=1.0, clock=clock) == (2.0, 2)
    assert clock.runs == 3
