from datetime import UTC, datetime

import pytest

from managed_deepagents._schedule_time import schedule_timing

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_one_time_window_and_recurring_expiration_clear() -> None:
    assert schedule_timing(at="2026-03-01T09:00-05:00", now=NOW) == {
        "schedule": "0 14 1 3 *",
        "timezone": "UTC",
        "at": "2026-03-01T14:00:00.000Z",
        "end_time": "2026-03-01T14:05:00.000Z",
    }
    assert schedule_timing(cron="0 9 * * MON-FRI", now=NOW) == {
        "schedule": "0 9 * * MON-FRI",
        "timezone": "UTC",
        "end_time": None,
    }


@pytest.mark.parametrize(
    "options",
    [
        {"at": "2026-03-08T02:30", "timezone": "America/New_York"},
        {"at": "2026-11-01T01:30", "timezone": "America/New_York"},
        {"at": "2026-03-01T09:00+01:00", "timezone": "America/New_York"},
        {"at": "2026-02-30T09:00Z"},
        {"at": "2026-03-01T09:00Z", "timezone": "UTC"},
        {"at": datetime(2026, 3, 1, 9, tzinfo=UTC).replace(tzinfo=None)},
        {"at": "2026-01-01T00:00Z"},
        {"at": "2027-01-01T00:01Z"},
        {"at": "2026-03-01T09:00"},
        {"cron": "60 * * * *"},
        {"cron": "* * * * *", "timezone": "invalid"},
        {"cron": "* * * * *", "timezone": ""},
        {"cron": "* * * * *", "at": "2026-03-01T09:00Z"},
    ],
)
def test_invalid_timing(options: dict[str, str]) -> None:
    with pytest.raises(ValueError, match=r"at|cron|timezone"):
        schedule_timing(**options, now=NOW)


def test_explicit_offset_resolves_repeated_minute() -> None:
    assert schedule_timing(at="2026-11-01T01:30-05:00", now=NOW)["at"] == "2026-11-01T06:30:00.000Z"


@pytest.mark.parametrize(
    "at", ["2026-03-01T23:59:59.001Z", datetime(2026, 3, 1, 23, 59, 59, 1000, tzinfo=UTC)]
)
def test_rounds_precise_instant_up_to_next_utc_minute(at: str | datetime) -> None:
    assert schedule_timing(at=at, now=NOW) == {
        "schedule": "0 0 2 3 *",
        "timezone": "UTC",
        "at": "2026-03-02T00:00:00.000Z",
        "end_time": "2026-03-02T00:05:00.000Z",
    }


def test_future_bound_applies_to_rounded_target() -> None:
    with pytest.raises(ValueError, match="365 days"):
        schedule_timing(at="2027-01-01T00:00:30Z", now=NOW.replace(second=30))
