from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance.report_orchestration import validate_monthly_window, validate_weekly_window


# ---------------------------------------------------------------------------
# validate_weekly_window
# ---------------------------------------------------------------------------

def test_validate_weekly_window_7_day_span_passes():
    validate_weekly_window("2026-03-09", "2026-03-15")


def test_validate_weekly_window_6_day_span_raises():
    with pytest.raises(InvalidInputError, match="exactly 7 days"):
        validate_weekly_window("2026-03-10", "2026-03-15")


def test_validate_weekly_window_8_day_span_raises():
    with pytest.raises(InvalidInputError, match="exactly 7 days"):
        validate_weekly_window("2026-03-08", "2026-03-15")


def test_validate_weekly_window_invalid_iso_date_raises():
    with pytest.raises(InvalidInputError, match="Invalid ISO date"):
        validate_weekly_window("not-a-date", "2026-03-15")


def test_validate_weekly_window_start_after_end_raises():
    with pytest.raises(InvalidInputError, match="on or before"):
        validate_weekly_window("2026-03-15", "2026-03-09")


# ---------------------------------------------------------------------------
# validate_monthly_window
# ---------------------------------------------------------------------------

def test_validate_monthly_window_full_march_passes():
    validate_monthly_window("2026-03-01", "2026-03-31")


def test_validate_monthly_window_full_february_passes():
    validate_monthly_window("2026-02-01", "2026-02-28")


def test_validate_monthly_window_leap_year_february_passes():
    validate_monthly_window("2024-02-01", "2024-02-29")


def test_validate_monthly_window_start_not_first_of_month_raises():
    with pytest.raises(InvalidInputError, match="full calendar month"):
        validate_monthly_window("2026-03-02", "2026-03-31")


def test_validate_monthly_window_end_not_last_of_month_raises():
    with pytest.raises(InvalidInputError, match="full calendar month"):
        validate_monthly_window("2026-03-01", "2026-03-30")
