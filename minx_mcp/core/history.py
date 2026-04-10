from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from minx_mcp.contracts import InvalidInputError
from minx_mcp.db import get_connection


def get_insight_history(
    db_path: str | Path,
    *,
    days: int = 28,
    insight_type: str | None = None,
    goal_id: int | None = None,
    end_date: str | None = None,
) -> dict[str, object]:
    if days < 1 or days > 90:
        raise InvalidInputError("days must be between 1 and 90")
    effective_end = end_date or date.today().isoformat()
    try:
        end_day = date.fromisoformat(effective_end)
    except ValueError as exc:
        raise InvalidInputError("end_date must be a valid ISO date") from exc
    start_day = end_day - timedelta(days=days - 1)

    conn = get_connection(Path(db_path))
    try:
        rows = conn.execute(
            """
            SELECT review_date, insight_type, dedupe_key, summary, supporting_signals,
                   confidence, severity, actionability, source
            FROM insights
            WHERE review_date >= ? AND review_date <= ?
              AND source = 'detector'
            ORDER BY review_date DESC, created_at DESC, id DESC
            """,
            (start_day.isoformat(), end_day.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    insights: list[dict[str, object]] = []
    pattern_counter: Counter[tuple[str, str]] = Counter()
    for row in rows:
        if insight_type is not None and row["insight_type"] != insight_type:
            continue
        dedupe_key = str(row["dedupe_key"])
        if goal_id is not None and not _dedupe_key_matches_goal(dedupe_key, goal_id):
            continue
        pattern_key = _pattern_key(dedupe_key)
        pattern_counter[(str(row["insight_type"]), pattern_key)] += 1
        insights.append(
            {
                "review_date": str(row["review_date"]),
                "insight_type": str(row["insight_type"]),
                "dedupe_key": dedupe_key,
                "summary": str(row["summary"]),
                "supporting_signals": json.loads(str(row["supporting_signals"])),
                "confidence": float(row["confidence"]),
                "severity": str(row["severity"]),
                "actionability": str(row["actionability"]),
            }
        )

    recurrences = [
        {
            "insight_type": insight_type_value,
            "pattern_key": pattern_key,
            "occurrences": count,
            "window_days": days,
            "description": f"Occurred {count} times in the last {days} days",
        }
        for (insight_type_value, pattern_key), count in pattern_counter.items()
    ]
    recurrences.sort(key=lambda item: (-int(item["occurrences"]), str(item["pattern_key"])))

    return {
        "window": {
            "start_date": start_day.isoformat(),
            "end_date": end_day.isoformat(),
            "days": days,
        },
        "insights": insights,
        "recurrences": recurrences,
    }


def _pattern_key(dedupe_key: str) -> str:
    _prefix, _sep, rest = dedupe_key.partition(":")
    return rest if rest else dedupe_key


def _dedupe_key_matches_goal(dedupe_key: str, goal_id: int) -> bool:
    pattern = re.compile(rf"(^|:)(goal-{goal_id}|goal-risk:{goal_id})(:|$)")
    return bool(pattern.search(_pattern_key(dedupe_key)))
