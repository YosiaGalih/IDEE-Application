"""The usage period of the dashboard (WP-2.1e): the UTC calendar month, half open."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.services.dashboard import current_period


def test_current_period_is_the_utc_month() -> None:
    assert current_period(datetime(2026, 11, 30, 23, 59, 59, tzinfo=UTC)) == (
        datetime(2026, 11, 1, tzinfo=UTC),
        datetime(2026, 12, 1, tzinfo=UTC),
    )
    assert current_period(datetime(2026, 12, 15, tzinfo=UTC)) == (
        datetime(2026, 12, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )


def test_a_local_time_is_read_in_utc() -> None:
    # 1 Dec 06:00 in Jakarta is still 30 Nov 23:00 UTC.
    jakarta = timezone(timedelta(hours=7))
    assert current_period(datetime(2026, 12, 1, 6, 0, tzinfo=jakarta))[0] == datetime(2026, 11, 1, tzinfo=UTC)
