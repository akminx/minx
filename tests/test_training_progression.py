from __future__ import annotations

from minx_mcp.training.progression import adherence_signal_for_window


def test_adherence_signal_for_window_thresholds() -> None:
    assert adherence_signal_for_window(sessions_logged=0, lookback_days=7) == "low"
    assert adherence_signal_for_window(sessions_logged=1, lookback_days=7) == "low"
    assert adherence_signal_for_window(sessions_logged=2, lookback_days=7) == "steady"
    assert adherence_signal_for_window(sessions_logged=3, lookback_days=7) == "on_track"


def test_adherence_signal_for_window_invalid_window() -> None:
    assert adherence_signal_for_window(sessions_logged=2, lookback_days=0) == "unknown"
