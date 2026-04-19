from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.db import get_connection
from minx_mcp.preferences import set_preference
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


def test_local_calendar_date_requires_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone"):
        local_calendar_date_for_utc_timestamp("2026-01-15T05:00:00", "America/New_York")


def test_resolve_timezone_name_rejects_bogus_iana(tmp_path) -> None:
    from minx_mcp.time_utils import resolve_timezone_name

    conn = get_connection(tmp_path / "minx.db")
    set_preference(conn, "core", "timezone", "Mars/Base")

    with pytest.raises(InvalidInputError, match="Invalid timezone"):
        resolve_timezone_name(conn)
