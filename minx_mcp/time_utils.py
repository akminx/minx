from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from sqlite3 import Connection
from zoneinfo import ZoneInfo

from minx_mcp.preferences import get_preference


def utc_now_isoformat(*, timespec: str = "microseconds") -> str:
    return format_utc_timestamp(datetime.now(UTC), timespec=timespec)


def format_utc_timestamp(value: datetime, *, timespec: str = "microseconds") -> str:
    return value.astimezone(UTC).isoformat(timespec=timespec).replace("+00:00", "Z")


def normalize_utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp must include timezone information")
    return format_utc_timestamp(parsed)


def resolve_timezone_name(conn: Connection) -> str:
    configured = get_preference(conn, "core", "timezone", None)
    if isinstance(configured, str) and configured:
        return configured
    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    return key if isinstance(key, str) and key else "UTC"


def local_day_utc_bounds(review_date: str, timezone_name: str) -> tuple[str, str]:
    zone = ZoneInfo(timezone_name)
    local_day = date.fromisoformat(review_date)
    local_start = datetime.combine(local_day, datetime.min.time(), tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    return format_utc_timestamp(local_start), format_utc_timestamp(local_end)


def local_calendar_date_for_utc_timestamp(occurred_at: str, timezone_name: str) -> str:
    """Map a UTC (or offset) ISO timestamp to the calendar date in ``timezone_name``."""
    normalized = occurred_at.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local_dt = parsed.astimezone(ZoneInfo(timezone_name))
    return local_dt.date().isoformat()


def next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()
