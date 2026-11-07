"""Helpers every builtin shares (spec 3.1d2a Goals 8, 9, 11)."""

from __future__ import annotations

from app.tools.builtin._common import partition, within
from app.workspace.policy import Budget


def never(parts: tuple[str, ...], budget: Budget) -> bool:
    return False


def test_sandbox_output_is_hidden_and_counted() -> None:
    """Goal 8: ``.kk/`` is never shown, and how many were left out is said."""
    got = partition([".kk/report.json", "src/a.py", ".kk/log.txt"], never)
    assert (got.shown, got.hidden, got.unchecked) == (["src/a.py"], 2, 0)


def test_names_past_the_cap_are_unchecked_not_shown() -> None:
    got = partition(["a", "b", "c"], never, cap=2)
    assert (got.shown, got.hidden, got.unchecked) == (["a", "b"], 0, 1)


def test_within_is_the_path_or_below_it() -> None:
    assert within("src/a.py", ["src"]) and within("src", ["src"]) and within("x", ["."])
    assert not within("srcx/a.py", ["src"])
