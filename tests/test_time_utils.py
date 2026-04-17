from __future__ import annotations

from minx_mcp.time_utils import local_calendar_date_for_utc_timestamp, local_day_utc_bounds


def test_local_day_utc_bounds_america_new_york() -> None:
    start_utc, end_utc = local_day_utc_bounds("2026-01-15", "America/New_York")
    assert start_utc == "2026-01-15T05:00:00.000000Z"
    assert end_utc == "2026-01-16T05:00:00.000000Z"


def test_local_calendar_date_for_utc_timestamp_america_new_york() -> None:
    assert (
        local_calendar_date_for_utc_timestamp("2026-01-15T04:59:59Z", "America/New_York")
        == "2026-01-14"
    )
    assert (
        local_calendar_date_for_utc_timestamp("2026-01-15T05:00:00Z", "America/New_York")
        == "2026-01-15"
    )
