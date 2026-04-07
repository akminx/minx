from __future__ import annotations

from minx_mcp.core.models import (
    DailyTimeline,
    InsightCandidate,
    OpenLoop,
    OpenLoopsSnapshot,
    ReadModels,
    SpendingSnapshot,
)


def test_detect_spending_spike_returns_empty_below_threshold():
    read_models = _build_read_models(vs_prior_week_pct=24.99)

    from minx_mcp.core.detectors import detect_spending_spike

    assert detect_spending_spike(read_models) == []


def test_detect_spending_spike_returns_warning_at_twenty_five_percent():
    read_models = _build_read_models(
        vs_prior_week_pct=25.0,
        by_category={"Groceries": 3500, "Dining Out": 2500},
    )

    from minx_mcp.core.detectors import detect_spending_spike

    insights = detect_spending_spike(read_models)

    assert [_simplify(insight) for insight in insights] == [
        (
            "finance.spending_spike",
            "2026-03-15:spending_spike:groceries",
            "warning",
            "suggestion",
                [
                    "Spending increased 25.0% versus the prior week.",
                    "Top spending category today: Groceries ($35.00).",
                ],
            )
        ]


def test_detect_spending_spike_returns_alert_at_fifty_percent():
    read_models = _build_read_models(vs_prior_week_pct=50.0)

    from minx_mcp.core.detectors import detect_spending_spike

    insights = detect_spending_spike(read_models)

    assert len(insights) == 1
    assert insights[0].severity == "alert"


def test_detect_spending_spike_returns_empty_for_cold_start():
    read_models = _build_read_models(vs_prior_week_pct=None)

    from minx_mcp.core.detectors import detect_spending_spike

    assert detect_spending_spike(read_models) == []


def test_detect_spending_spike_adds_dominant_category_signal_when_one_category_drives_change():
    read_models = _build_read_models(
        total_spent_cents=10000,
        vs_prior_week_pct=40.0,
        by_category={"Groceries": 7000, "Dining Out": 2000, "Shopping": 1000},
    )

    from minx_mcp.core.detectors import detect_spending_spike

    insights = detect_spending_spike(read_models)

    assert len(insights) == 1
    assert insights[0].supporting_signals == [
        "Spending increased 40.0% versus the prior week.",
        "Groceries drove 70% of today's spending.",
    ]


def test_detect_open_loops_returns_one_insight_per_loop():
    read_models = _build_read_models(
        open_loops=[
            OpenLoop(
                domain="finance",
                loop_type="uncategorized_transactions",
                description="2 uncategorized transactions totaling $35.25",
                count=2,
                severity="info",
            ),
            OpenLoop(
                domain="finance",
                loop_type="failed_import_job",
                description="Import job job-failed failed for /imports/a.csv",
                count=1,
                severity="warning",
            ),
            OpenLoop(
                domain="finance",
                loop_type="stale_import_job",
                description="Import job job-stale is stale for /imports/b.csv",
                count=1,
                severity="warning",
            ),
        ]
    )

    from minx_mcp.core.detectors import detect_open_loops

    insights = detect_open_loops(read_models)

    assert [_simplify(insight) for insight in insights] == [
        (
            "finance.open_loop",
            "2026-03-15:open_loop:uncategorized_transactions:uncategorized",
            "info",
            "suggestion",
            ["2 uncategorized transactions totaling $35.25"],
        ),
        (
            "finance.open_loop",
            "2026-03-15:open_loop:failed_import_job:import-job-job-failed",
            "warning",
            "action_needed",
            ["Import job job-failed failed for /imports/a.csv"],
        ),
        (
            "finance.open_loop",
            "2026-03-15:open_loop:stale_import_job:import-job-job-stale",
            "warning",
            "action_needed",
            ["Import job job-stale is stale for /imports/b.csv"],
        ),
    ]


def test_detect_open_loops_uses_loop_severity_mapping():
    read_models = _build_read_models(
        open_loops=[
            OpenLoop(
                domain="finance",
                loop_type="uncategorized_transactions",
                description="25 uncategorized transactions totaling $200.00",
                count=25,
                severity="warning",
            ),
            OpenLoop(
                domain="finance",
                loop_type="failed_import_job",
                description="Import job job-failed failed for /imports/a.csv",
                count=1,
                severity="warning",
            ),
        ]
    )

    from minx_mcp.core.detectors import detect_open_loops

    insights = detect_open_loops(read_models)

    assert [insight.severity for insight in insights] == ["warning", "warning"]
    assert [insight.actionability for insight in insights] == [
        "suggestion",
        "action_needed",
    ]


def test_detect_open_loops_returns_empty_when_none_exist():
    read_models = _build_read_models(open_loops=[])

    from minx_mcp.core.detectors import detect_open_loops

    assert detect_open_loops(read_models) == []


def test_detector_dedupe_keys_remain_stable_when_summary_wording_changes():
    from minx_mcp.core.detectors import detect_open_loops, detect_spending_spike

    first_spending = detect_spending_spike(
        _build_read_models(
            vs_prior_week_pct=40.0,
            by_category={"Groceries": 7000, "Dining Out": 3000},
        )
    )
    second_spending = detect_spending_spike(
        _build_read_models(
            vs_prior_week_pct=40.0,
            by_category={"Groceries": 7000, "Dining Out": 3000},
        )
    )
    first_open_loops = detect_open_loops(
        _build_read_models(
            open_loops=[
                OpenLoop(
                    domain="finance",
                    loop_type="failed_import_job",
                    description="Import job job-failed failed for /imports/a.csv",
                    count=1,
                    severity="warning",
                )
            ]
        )
    )
    second_open_loops = detect_open_loops(
        _build_read_models(
            open_loops=[
                OpenLoop(
                    domain="finance",
                    loop_type="failed_import_job",
                    description="Finance import job job-failed needs attention for /imports/a.csv",
                    count=1,
                    severity="warning",
                )
            ]
        )
    )

    assert first_spending[0].dedupe_key == second_spending[0].dedupe_key
    assert first_open_loops[0].dedupe_key == second_open_loops[0].dedupe_key


def test_detectors_registry_is_in_spec_order():
    from minx_mcp.core.detectors import DETECTORS

    assert [detector.__name__ for detector in DETECTORS] == [
        "detect_spending_spike",
        "detect_open_loops",
    ]


def _build_read_models(
    *,
    total_spent_cents: int = 6000,
    by_category: dict[str, int] | None = None,
    vs_prior_week_pct: float | None = 25.0,
    open_loops: list[OpenLoop] | None = None,
) -> ReadModels:
    return ReadModels(
        timeline=DailyTimeline(date="2026-03-15", entries=[]),
        spending=SpendingSnapshot(
            date="2026-03-15",
            total_spent_cents=total_spent_cents,
            by_category=by_category or {"Groceries": 4000, "Dining Out": 2000},
            top_merchants=[],
            vs_prior_week_pct=vs_prior_week_pct,
            uncategorized_count=0,
            uncategorized_total_cents=0,
        ),
        open_loops=OpenLoopsSnapshot(
            date="2026-03-15",
            loops=open_loops or [],
        ),
    )


def _simplify(insight: InsightCandidate) -> tuple[str, str, str, str, list[str]]:
    return (
        insight.insight_type,
        insight.dedupe_key,
        insight.severity,
        insight.actionability,
        insight.supporting_signals,
    )
