from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta
from sqlite3 import Connection
from zoneinfo import ZoneInfo

from minx_mcp.core.models import TrainingSnapshot
from minx_mcp.preferences import get_preference
from minx_mcp.time_utils import format_utc_timestamp, normalize_utc_timestamp
from minx_mcp.training.progression import adherence_signal_for_window


class TrainingReadAPI:
    def __init__(self, db: Connection) -> None:
        self._db = db

    def get_training_summary(self, date: str) -> TrainingSnapshot:
        lookback_days = 7
        start_date = (
            date_cls.fromisoformat(date) - timedelta(days=lookback_days - 1)
        ).isoformat()
        timezone_name = _resolve_timezone_name(self._db)
        start_utc, _ = _local_day_utc_bounds(start_date, timezone_name)
        _, end_utc = _local_day_utc_bounds(date, timezone_name)
        rows = self._db.execute(
            """
            SELECT
                s.id,
                s.occurred_at,
                COUNT(ss.id) AS set_count,
                COALESCE(SUM(COALESCE(ss.reps, 0) * COALESCE(ss.weight_kg, 0)), 0.0) AS total_volume_kg
            FROM training_sessions s
            LEFT JOIN training_session_sets ss ON ss.session_id = s.id
            GROUP BY s.id
            ORDER BY s.occurred_at ASC, s.id ASC
            """
        ).fetchall()
        normalized_rows = [
            (normalize_utc_timestamp(str(row["occurred_at"])), row)
            for row in rows
        ]
        windowed_rows = [
            row
            for occurred_at, row in sorted(normalized_rows, key=lambda item: (item[0], int(item[1]["id"])))
            if start_utc <= occurred_at < end_utc
        ]
        sessions_logged = len(windowed_rows)
        return TrainingSnapshot(
            date=date,
            sessions_logged=sessions_logged,
            total_sets=sum(int(row["set_count"]) for row in windowed_rows),
            total_volume_kg=sum(float(row["total_volume_kg"]) for row in windowed_rows),
            last_session_at=str(windowed_rows[-1]["occurred_at"]) if windowed_rows else None,
            adherence_signal=adherence_signal_for_window(
                sessions_logged=sessions_logged,
                lookback_days=lookback_days,
            ),
        )


def _resolve_timezone_name(conn: Connection) -> str:
    configured = get_preference(conn, "core", "timezone", None)
    if isinstance(configured, str) and configured:
        return configured
    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    return key if isinstance(key, str) and key else "UTC"


def _local_day_utc_bounds(review_date: str, timezone_name: str) -> tuple[str, str]:
    zone = ZoneInfo(timezone_name)
    local_day = date_cls.fromisoformat(review_date)
    local_start = datetime.combine(local_day, datetime.min.time(), tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    return format_utc_timestamp(local_start), format_utc_timestamp(local_end)
