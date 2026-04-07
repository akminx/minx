from __future__ import annotations

from datetime import datetime, timezone


def utc_now_isoformat(*, timespec: str = "microseconds") -> str:
    return format_utc_timestamp(datetime.now(timezone.utc), timespec=timespec)


def format_utc_timestamp(value: datetime, *, timespec: str = "microseconds") -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec=timespec)
        .replace("+00:00", "Z")
    )


def normalize_utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp must include timezone information")
    return format_utc_timestamp(parsed)
