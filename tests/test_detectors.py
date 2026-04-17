from __future__ import annotations

from dataclasses import dataclass

from minx_mcp.core.models import (
    DailyTimeline,
    FinanceReadInterface,
    GoalProgress,
    InsightCandidate,
    OpenLoop,
    OpenLoopsSnapshot,
    ReadModels,
    SpendingSnapshot,
)


def test_detect_spending_spike_returns_empty_below_threshold():
    read_models = _build_read_models(vs_prior_week_pct=24.99)

    from minx_mcp.core.detectors import detect_spending_spike

    assert detect_spending_spike(read_models).insights == ()


def test_detect_spending_spike_returns_warning_at_twenty_five_percent():
    read_models = _build_read_models(
        vs_prior_week_pct=25.0,
        by_category={"Groceries": 3500, "Dining Out": 2500},
    )

    from minx_mcp.core.detectors import detect_spending_spike

    insights = detect_spending_spike(read_models).insights

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

    insights = detect_spending_spike(read_models).insights

    assert len(insights) == 1
    assert insights[0].severity == "alert"


def test_detect_spending_spike_returns_empty_for_cold_start():
    read_models = _build_read_models(vs_prior_week_pct=None)

    from minx_mcp.core.detectors import detect_spending_spike

    assert detect_spending_spike(read_models).insights == ()


def test_detect_spending_spike_adds_dominant_category_signal_when_one_category_drives_change():
    read_models = _build_read_models(
        total_spent_cents=10000,
        vs_prior_week_pct=40.0,
        by_category={"Groceries": 7000, "Dining Out": 2000, "Shopping": 1000},
    )

    from minx_mcp.core.detectors import detect_spending_spike

    insights = detect_spending_spike(read_models).insights

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

    insights = detect_open_loops(read_models).insights

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

    insights = detect_open_loops(read_models).insights

    assert [insight.severity for insight in insights] == ["warning", "warning"]
    assert [insight.actionability for insight in insights] == [
        "suggestion",
        "action_needed",
    ]


def test_detect_open_loops_returns_empty_when_none_exist():
    read_models = _build_read_models(open_loops=[])

    from minx_mcp.core.detectors import detect_open_loops

    assert detect_open_loops(read_models).insights == ()


def test_detector_dedupe_keys_remain_stable_when_summary_wording_changes():
    from minx_mcp.core.detectors import detect_open_loops, detect_spending_spike

    first_spending = detect_spending_spike(
        _build_read_models(
            vs_prior_week_pct=40.0,
            by_category={"Groceries": 7000, "Dining Out": 3000},
        )
    ).insights
    second_spending = detect_spending_spike(
        _build_read_models(
            vs_prior_week_pct=40.0,
            by_category={"Groceries": 7000, "Dining Out": 3000},
        )
    ).insights
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
    ).insights
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
    ).insights

    assert first_spending[0].dedupe_key == second_spending[0].dedupe_key
    assert first_open_loops[0].dedupe_key == second_open_loops[0].dedupe_key


def test_detect_goal_drift_returns_off_track_goal_insight():
    read_models = _build_read_models(
        goal_progress=[
            GoalProgress(
                goal_id=1,
                title="Dining out under $250",
                metric_type="sum_below",
                target_value=25_000,
                actual_value=26_000,
                remaining_value=0,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="off_track",
                summary="Off track: $260.00 of $250.00 $0.00 remaining.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ]
    )

    from minx_mcp.core.goal_detectors import detect_goal_drift

    insights = detect_goal_drift(read_models).insights

    assert len(insights) == 1
    assert insights[0].insight_type == "core.goal_drift"
    assert insights[0].severity == "warning"
    assert insights[0].actionability == "action_needed"
    assert "goal-1" in insights[0].dedupe_key


def test_detect_goal_drift_returns_empty_for_on_track_goals():
    read_models = _build_read_models(
        goal_progress=[
            GoalProgress(
                goal_id=1,
                title="Dining out under $250",
                metric_type="sum_below",
                target_value=25_000,
                actual_value=5_000,
                remaining_value=20_000,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="on_track",
                summary="On track.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ]
    )

    from minx_mcp.core.goal_detectors import detect_goal_drift

    assert detect_goal_drift(read_models).insights == ()


def test_detect_category_drift_returns_alert_for_real_baseline_increase():
    read_models = _build_read_models(
        finance_api=_FinanceAPIDouble(
            total_map={
                ("2026-03-01", "2026-03-15", ("Dining Out",)): 15_000,
                ("2026-02-14", "2026-02-28", ("Dining Out",)): 10_000,
            }
        ),
        goal_progress=[
            GoalProgress(
                goal_id=1,
                title="Dining out under $250",
                metric_type="sum_below",
                target_value=25_000,
                actual_value=26_000,
                remaining_value=0,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="off_track",
                summary="Off track.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ],
    )

    from minx_mcp.core.goal_detectors import detect_category_drift

    insights = detect_category_drift(read_models).insights

    assert len(insights) == 1
    assert insights[0].insight_type == "finance.category_drift"
    assert insights[0].severity == "alert"
    assert insights[0].actionability == "action_needed"


def test_detect_category_drift_returns_warning_for_real_baseline_increase():
    read_models = _build_read_models(
        finance_api=_FinanceAPIDouble(
            total_map={
                ("2026-03-01", "2026-03-15", ("Groceries",)): 12_500,
                ("2026-02-14", "2026-02-28", ("Groceries",)): 10_000,
            }
        ),
        goal_progress=[
            GoalProgress(
                goal_id=2,
                title="Groceries under $400",
                metric_type="sum_below",
                target_value=40_000,
                actual_value=12_500,
                remaining_value=27_500,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="on_track",
                summary="On track.",
                category_names=["Groceries"],
                merchant_names=[],
                account_names=[],
            )
        ],
    )

    from minx_mcp.core.goal_detectors import detect_category_drift

    insights = detect_category_drift(read_models).insights

    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert insights[0].actionability == "suggestion"
    assert insights[0].supporting_signals == [
        "Current span: $125.00",
        "Prior span: $100.00",
        "Delta: $25.00 (1.25x prior)",
        "Goal: On track.",
    ]


def test_detect_category_drift_does_not_fire_when_goal_is_merely_watch_without_measured_drift():
    read_models = _build_read_models(
        finance_api=_FinanceAPIDouble(
            total_map={
                ("2026-03-01", "2026-03-15", ("Dining Out",)): 10_000,
                ("2026-02-14", "2026-02-28", ("Dining Out",)): 9_500,
            }
        ),
        goal_progress=[
            GoalProgress(
                goal_id=1,
                title="Test",
                metric_type="sum_below",
                target_value=25_000,
                actual_value=10_000,
                remaining_value=15_000,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="watch",
                summary="Watch.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ],
    )

    from minx_mcp.core.goal_detectors import detect_category_drift

    assert detect_category_drift(read_models).insights == ()


def test_detect_category_drift_does_not_fire_on_cold_start_baseline():
    read_models = _build_read_models(
        finance_api=_FinanceAPIDouble(
            total_map={
                ("2026-03-01", "2026-03-15", ("Dining Out",)): 20_000,
                ("2026-02-14", "2026-02-28", ("Dining Out",)): 0,
            }
        ),
        goal_progress=[
            GoalProgress(
                goal_id=3,
                title="Dining Out",
                metric_type="sum_below",
                target_value=25_000,
                actual_value=20_000,
                remaining_value=5_000,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="watch",
                summary="Watch.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ],
    )

    from minx_mcp.core.goal_detectors import detect_category_drift

    assert detect_category_drift(read_models).insights == ()


def test_detect_category_drift_uses_count_thresholds_for_warning_and_alert():
    warning_models = _build_read_models(
        finance_api=_FinanceAPIDouble(
            count_map={
                ("2026-03-01", "2026-03-15", ("Dining Out",)): 6,
                ("2026-02-14", "2026-02-28", ("Dining Out",)): 4,
            }
        ),
        goal_progress=[
            GoalProgress(
                goal_id=4,
                title="Fewer dining trips",
                metric_type="count_below",
                target_value=10,
                actual_value=6,
                remaining_value=4,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="on_track",
                summary="On track.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ],
    )
    alert_models = _build_read_models(
        finance_api=_FinanceAPIDouble(
            count_map={
                ("2026-03-01", "2026-03-15", ("Dining Out",)): 8,
                ("2026-02-14", "2026-02-28", ("Dining Out",)): 4,
            }
        ),
        goal_progress=[
            GoalProgress(
                goal_id=5,
                title="Fewer dining trips",
                metric_type="count_below",
                target_value=10,
                actual_value=8,
                remaining_value=2,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="watch",
                summary="Watch.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ],
    )

    from minx_mcp.core.goal_detectors import detect_category_drift

    warning = detect_category_drift(warning_models).insights
    alert = detect_category_drift(alert_models).insights

    assert len(warning) == 1
    assert warning[0].severity == "warning"
    assert len(alert) == 1
    assert alert[0].severity == "alert"


def test_detect_category_drift_supports_merchant_only_goals():
    read_models = _build_read_models(
        finance_api=_FinanceAPIDouble(
            total_map={
                (
                    "2026-03-01",
                    "2026-03-15",
                    (),
                    ("Cafe",),
                    (),
                ): 12_000,
                (
                    "2026-02-14",
                    "2026-02-28",
                    (),
                    ("Cafe",),
                    (),
                ): 8_000,
            }
        ),
        goal_progress=[
            GoalProgress(
                goal_id=6,
                title="Cafe spend under $200",
                metric_type="sum_below",
                target_value=20_000,
                actual_value=12_000,
                remaining_value=8_000,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="watch",
                summary="Watch.",
                category_names=[],
                merchant_names=["Cafe"],
                account_names=[],
            )
        ],
    )

    from minx_mcp.core.goal_detectors import detect_category_drift

    insights = detect_category_drift(read_models).insights

    assert len(insights) == 1
    assert insights[0].insight_type == "finance.category_drift"
    assert insights[0].summary == (
        "Cafe spending is up versus the prior comparable span for Cafe spend under $200."
    )


def test_detect_category_drift_supports_account_only_goals():
    read_models = _build_read_models(
        finance_api=_FinanceAPIDouble(
            count_map={
                (
                    "2026-03-01",
                    "2026-03-15",
                    (),
                    (),
                    ("DCU",),
                ): 6,
                (
                    "2026-02-14",
                    "2026-02-28",
                    (),
                    (),
                    ("DCU",),
                ): 4,
            }
        ),
        goal_progress=[
            GoalProgress(
                goal_id=7,
                title="Fewer DCU purchases",
                metric_type="count_below",
                target_value=10,
                actual_value=6,
                remaining_value=4,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="watch",
                summary="Watch.",
                category_names=[],
                merchant_names=[],
                account_names=["DCU"],
            )
        ],
    )

    from minx_mcp.core.goal_detectors import detect_category_drift

    insights = detect_category_drift(read_models).insights

    assert len(insights) == 1
    assert insights[0].summary == (
        "DCU activity is up versus the prior comparable span for Fewer DCU purchases."
    )


def test_detector_result_preserves_spending_spike_insights():
    read_models = _build_read_models(
        vs_prior_week_pct=25.0,
        by_category={"Groceries": 3500, "Dining Out": 2500},
    )
    from minx_mcp.core.detectors import detect_spending_spike

    result = detect_spending_spike(read_models)
    assert result.memory_proposals == ()
    assert [_simplify(i) for i in result.insights] == [
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


def _build_read_models(
    *,
    total_spent_cents: int = 6000,
    by_category: dict[str, int] | None = None,
    vs_prior_week_pct: float | None = 25.0,
    open_loops: list[OpenLoop] | None = None,
    goal_progress: list[GoalProgress] | None = None,
    finance_api: FinanceReadInterface | None = None,
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
        goal_progress=goal_progress or [],
        finance_api=finance_api,
    )


def _simplify(insight: InsightCandidate) -> tuple[str, str, str, str, list[str]]:
    return (
        insight.insight_type,
        insight.dedupe_key,
        insight.severity,
        insight.actionability,
        insight.supporting_signals,
    )


@dataclass
class _FinanceAPIDouble:
    total_map: (
        dict[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], int] | None
    ) = None
    count_map: (
        dict[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]], int] | None
    ) = None

    def get_filtered_spending_total(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int:
        key = (
            start_date,
            end_date,
            tuple(category_names or ()),
            tuple(merchant_names or ()),
            tuple(account_names or ()),
        )
        total_map = self.total_map or {}
        return total_map.get(
            key,
            total_map.get((start_date, end_date, tuple(category_names or ())), 0),
        )

    def get_filtered_transaction_count(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int:
        key = (
            start_date,
            end_date,
            tuple(category_names or ()),
            tuple(merchant_names or ()),
            tuple(account_names or ()),
        )
        count_map = self.count_map or {}
        return count_map.get(
            key,
            count_map.get((start_date, end_date, tuple(category_names or ())), 0),
        )
