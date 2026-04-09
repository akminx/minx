from __future__ import annotations

from minx_mcp.core.goal_progress import build_goal_progress
from minx_mcp.core.models import GoalRecord


class _GoalFinanceAPIDouble:
    def __init__(
        self,
        total: int = 0,
        count: int = 0,
        totals_by_window: dict[tuple[str, str], int] | None = None,
        counts_by_window: dict[tuple[str, str], int] | None = None,
    ) -> None:
        self._total = total
        self._count = count
        self._totals_by_window = totals_by_window or {}
        self._counts_by_window = counts_by_window or {}
        self.calls: list[tuple[str, str, str]] = []

    def get_filtered_spending_total(self, start_date, end_date, **kwargs) -> int:
        self.calls.append(("total", start_date, end_date))
        return self._totals_by_window.get((start_date, end_date), self._total)

    def get_filtered_transaction_count(self, start_date, end_date, **kwargs) -> int:
        self.calls.append(("count", start_date, end_date))
        return self._counts_by_window.get((start_date, end_date), self._count)


def _make_goal(**overrides) -> GoalRecord:
    defaults = dict(
        id=1,
        goal_type="spending_cap",
        title="Dining out under $250",
        status="active",
        metric_type="sum_below",
        target_value=25_000,
        period="monthly",
        domain="finance",
        category_names=["Dining Out"],
        merchant_names=[],
        account_names=[],
        starts_on="2026-03-01",
        ends_on=None,
        notes=None,
        created_at="2026-03-01T00:00:00Z",
        updated_at="2026-03-01T00:00:00Z",
    )
    defaults.update(overrides)
    return GoalRecord(**defaults)


def test_build_goal_progress_for_monthly_sum_below_on_track():
    goal = _make_goal(target_value=25_000)
    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=8_000, count=3),
    )

    assert len(progress) == 1
    assert progress[0].status == "on_track"
    assert progress[0].actual_value == 8_000
    assert progress[0].remaining_value == 17_000


def test_build_goal_progress_for_monthly_sum_below_off_track():
    goal = _make_goal(target_value=25_000)
    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=26_000, count=10),
    )

    assert len(progress) == 1
    assert progress[0].status == "off_track"
    assert progress[0].remaining_value == 0


def test_build_goal_progress_for_monthly_sum_below_watch():
    goal = _make_goal(target_value=25_000)
    # At day 15 of 31, elapsed fraction ~0.48, expected at point ~12000
    # actual of 11500 > 12000 * 0.9 = 10800 -> watch
    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=11_500, count=5),
    )

    assert len(progress) == 1
    assert progress[0].status == "watch"


def test_build_goal_progress_for_count_below():
    goal = _make_goal(metric_type="count_below", target_value=10)
    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=5000, count=3),
    )

    assert len(progress) == 1
    assert progress[0].actual_value == 3
    assert progress[0].status == "on_track"


def test_build_goal_progress_empty_goals_returns_empty():
    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[],
        finance_api=_GoalFinanceAPIDouble(),
    )
    assert progress == []


def test_build_goal_progress_weekly_period_window():
    goal = _make_goal(period="weekly", starts_on="2026-03-01")
    progress = build_goal_progress(
        review_date="2026-03-12",  # Thursday -> week starts Monday 2026-03-09
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=5_000, count=2),
    )

    assert len(progress) == 1
    assert progress[0].current_start == "2026-03-09"
    assert progress[0].current_end == "2026-03-15"


def test_build_goal_progress_mid_month_goal_start_excludes_pre_goal_spending():
    goal = _make_goal(starts_on="2026-03-10")
    finance_api = _GoalFinanceAPIDouble(
        totals_by_window={("2026-03-10", "2026-03-15"): 8_000},
        counts_by_window={("2026-03-10", "2026-03-15"): 3},
    )

    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=finance_api,
    )

    assert len(progress) == 1
    assert progress[0].current_start == "2026-03-10"
    assert progress[0].current_end == "2026-03-31"
    assert progress[0].actual_value == 8_000
    assert finance_api.calls == [
        ("total", "2026-03-10", "2026-03-15"),
        ("count", "2026-03-10", "2026-03-15"),
    ]


def test_build_goal_progress_ended_goal_window_clamps_at_ends_on():
    goal = _make_goal(ends_on="2026-03-20")
    finance_api = _GoalFinanceAPIDouble(
        totals_by_window={("2026-03-01", "2026-03-15"): 9_000},
        counts_by_window={("2026-03-01", "2026-03-15"): 4},
    )

    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=finance_api,
    )

    assert len(progress) == 1
    assert progress[0].current_start == "2026-03-01"
    assert progress[0].current_end == "2026-03-20"
    assert progress[0].actual_value == 9_000


def test_build_goal_progress_excludes_future_dated_transactions_after_review_date():
    goal = _make_goal()
    finance_api = _GoalFinanceAPIDouble(
        totals_by_window={
            ("2026-03-01", "2026-03-15"): 4_000,
            ("2026-03-01", "2026-03-31"): 9_000,
        },
        counts_by_window={
            ("2026-03-01", "2026-03-15"): 2,
            ("2026-03-01", "2026-03-31"): 5,
        },
    )

    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=finance_api,
    )

    assert len(progress) == 1
    assert progress[0].actual_value == 4_000
    assert finance_api.calls == [
        ("total", "2026-03-01", "2026-03-15"),
        ("count", "2026-03-01", "2026-03-15"),
    ]


def test_build_goal_progress_for_sum_above_can_reach_met_status():
    goal = _make_goal(metric_type="sum_above", target_value=10_000, title="Save $100")
    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=10_500, count=2),
    )

    assert len(progress) == 1
    assert progress[0].actual_value == 10_500
    assert progress[0].remaining_value is None
    assert progress[0].status == "met"
    assert progress[0].summary == "Met! $105.00 against target of $100.00."


def test_build_goal_progress_for_sum_above_can_be_off_track():
    goal = _make_goal(metric_type="sum_above", target_value=20_000, title="Save $200")
    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=5_000, count=1),
    )

    assert len(progress) == 1
    assert progress[0].status == "off_track"
    assert progress[0].summary == "Off track: $50.00 of $200.00."


def test_build_goal_progress_for_count_above_can_be_watch():
    goal = _make_goal(metric_type="count_above", target_value=10, title="Workout sessions")
    progress = build_goal_progress(
        review_date="2026-03-15",
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=0, count=4),
    )

    assert len(progress) == 1
    assert progress[0].actual_value == 4
    assert progress[0].remaining_value is None
    assert progress[0].status == "watch"
    assert progress[0].summary == "Watch: 4 of 10 — approaching limit."
