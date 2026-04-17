from __future__ import annotations

import json

from minx_mcp.core.detectors import DETECTORS, detect_open_loops
from minx_mcp.core.history import get_insight_history
from minx_mcp.core.models import (
    DailyTimeline,
    OpenLoop,
    OpenLoopsSnapshot,
    ReadModels,
    SpendingSnapshot,
)
from minx_mcp.core.snapshot import _persist_detector_insights
from minx_mcp.db import get_connection


def test_get_insight_history_returns_windowed_insights_and_recurrences(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.executemany(
        """
        INSERT INTO insights (
            insight_type, dedupe_key, summary, supporting_signals, confidence,
            severity, actionability, source, review_date, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, datetime('now'))
        """,
        [
            (
                "finance.spending_spike",
                "2026-03-10:spending_spike:dining-out",
                "Spike 1",
                json.dumps(["a"]),
                0.9,
                "warning",
                "suggestion",
                "detector",
                "2026-03-10",
            ),
            (
                "finance.spending_spike",
                "2026-03-17:spending_spike:dining-out",
                "Spike 2",
                json.dumps(["b"]),
                0.9,
                "warning",
                "suggestion",
                "detector",
                "2026-03-17",
            ),
        ],
    )
    conn.commit()
    conn.close()

    result = get_insight_history(db_path, days=28, end_date="2026-03-31")

    assert result["window"] == {
        "start_date": "2026-03-04",
        "end_date": "2026-03-31",
        "days": 28,
    }
    assert len(result["insights"]) == 2
    assert result["recurrences"] == [
        {
            "insight_type": "finance.spending_spike",
            "pattern_key": "spending_spike:dining-out",
            "occurrences": 2,
            "window_days": 28,
            "description": "Occurred 2 times in the last 28 days",
        }
    ]


def test_get_insight_history_filters_goal_ids_without_substring_collisions(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.executemany(
        """
        INSERT INTO insights (
            insight_type, dedupe_key, summary, supporting_signals, confidence,
            severity, actionability, source, review_date, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, datetime('now'))
        """,
        [
            (
                "core.goal_drift",
                "2026-03-10:goal_drift:goal-1",
                "Goal 1",
                json.dumps(["a"]),
                0.9,
                "warning",
                "suggestion",
                "detector",
                "2026-03-10",
            ),
            (
                "core.goal_drift",
                "2026-03-11:goal_drift:goal-10",
                "Goal 10",
                json.dumps(["b"]),
                0.9,
                "warning",
                "suggestion",
                "detector",
                "2026-03-11",
            ),
        ],
    )
    conn.commit()
    conn.close()

    result = get_insight_history(db_path, days=28, end_date="2026-03-31", goal_id=1)

    assert [item["summary"] for item in result["insights"]] == ["Goal 1"]


def test_get_insight_history_ignores_non_detector_rows(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.executemany(
        """
        INSERT INTO insights (
            insight_type, dedupe_key, summary, supporting_signals, confidence,
            severity, actionability, source, review_date, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, datetime('now'))
        """,
        [
            (
                "finance.spending_spike",
                "2026-03-10:spending_spike:dining-out",
                "Detector row",
                json.dumps(["a"]),
                0.9,
                "warning",
                "suggestion",
                "detector",
                "2026-03-10",
            ),
            (
                "finance.spending_spike",
                "2026-03-11:spending_spike:dining-out",
                "LLM row",
                json.dumps(["b"]),
                0.6,
                "info",
                "suggestion",
                "llm",
                "2026-03-11",
            ),
        ],
    )
    conn.commit()
    conn.close()

    result = get_insight_history(db_path, days=28, end_date="2026-03-31")

    assert [item["summary"] for item in result["insights"]] == ["Detector row"]
    assert result["recurrences"] == [
        {
            "insight_type": "finance.spending_spike",
            "pattern_key": "spending_spike:dining-out",
            "occurrences": 1,
            "window_days": 28,
            "description": "Occurred 1 times in the last 28 days",
        }
    ]


def test_get_insight_history_finds_open_loop_rows_by_detector_name(tmp_path):
    detector = next(d for d in DETECTORS if d.fn is detect_open_loops)
    review_date = "2026-03-15"
    read_models = ReadModels(
        timeline=DailyTimeline(date=review_date, entries=[]),
        spending=SpendingSnapshot(
            date=review_date,
            total_spent_cents=0,
            by_category={},
            top_merchants=[],
            vs_prior_week_pct=None,
            uncategorized_count=0,
            uncategorized_total_cents=0,
        ),
        open_loops=OpenLoopsSnapshot(
            date=review_date,
            loops=[
                OpenLoop(
                    domain="finance",
                    loop_type="uncategorized_transactions",
                    description="2 uncategorized transactions",
                    count=2,
                    severity="info",
                ),
            ],
        ),
        goal_progress=[],
        finance_api=None,
    )
    insights = list(detect_open_loops(read_models).insights)

    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _persist_detector_insights(conn, review_date, insights, force=False)
    conn.close()

    result = get_insight_history(
        db_path,
        days=28,
        end_date="2026-03-31",
        insight_type=detector.key,
    )

    assert len(result["insights"]) == 1
    assert result["insights"][0]["insight_type"] == detector.key
    assert result["insights"][0]["summary"] == "2 uncategorized transactions"
