from __future__ import annotations

GOOD_ADHERENCE_SESSIONS = 3
LOW_ADHERENCE_SESSIONS = 2


def adherence_signal_for_window(*, sessions_logged: int, lookback_days: int) -> str:
    if lookback_days <= 0:
        return "unknown"
    if sessions_logged >= GOOD_ADHERENCE_SESSIONS:
        return "on_track"
    if sessions_logged >= LOW_ADHERENCE_SESSIONS:
        return "steady"
    return "low"
