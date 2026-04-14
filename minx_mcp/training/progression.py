from __future__ import annotations


def adherence_signal_for_window(*, sessions_logged: int, lookback_days: int) -> str:
    if lookback_days <= 0:
        return "unknown"
    if sessions_logged >= 3:
        return "on_track"
    if sessions_logged >= 2:
        return "steady"
    return "low"

