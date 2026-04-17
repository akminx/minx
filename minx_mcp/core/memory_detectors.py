"""Memory-proposing detectors (Slice 6a).

Design notes (where the durable-memory spec is ambiguous vs this slice):

* ``detect_recurring_merchant_pattern`` uses four non-overlapping 7-day windows ending on
  ``ReadModels.spending.date``. A merchant must appear with outflow in at least three of those
  windows. Cadence is inferred from the mean spacing between sorted week indices (1 → weekly,
  ~2 → biweekly, ≥2.8 → monthly). ``typical_amount_cents`` is the median of per-window spend
  totals across hit windows. Confidence: total transaction count across the four windows
  (from ``MerchantSpending.transaction_count``) drives 0.65–0.9 (3 tx → 0.65, 4 → 0.72,
  5 → 0.78, 6+ → 0.9), capped by pattern strength.

* ``detect_category_preference`` uses trailing 30 days of categorized outflows; dominant
  category must be ≥40% of outflow. Confidence scales from 0.72 to 0.88 by share (40% → 0.72,
  100% → 0.88).

* ``detect_schedule_pattern`` uses ``MealsReadInterface.get_nutrition_summary`` for each of the
  last 28 local calendar days ending on ``spending.date``. A weekday counts when ``meal_count
  >= 2``. If any weekday has ≥3 hits and hit-rate ≥75% over occurrences of that weekday in the
  window, emit ``scope="meals"``, ``subject`` like ``tuesday_multi_meal`` (slug), confidence 0.78
  plus a small bump for extra hits (capped 0.9). If training is preferred but meal data is
  absent, this detector returns empty (training read API does not expose per-session DOW).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from statistics import median

from minx_mcp.core._utils import slugify
from minx_mcp.core.memory_models import DetectorResult, MemoryProposal
from minx_mcp.core.models import ReadModels
from minx_mcp.finance.normalization import normalize_merchant

_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def detect_recurring_merchant_pattern(read_models: ReadModels) -> DetectorResult:
    api = read_models.finance_api
    if api is None:
        return DetectorResult.empty()
    anchor = date.fromisoformat(read_models.spending.date)
    week_hits: dict[str, set[int]] = defaultdict(set)
    week_totals: dict[str, list[int]] = defaultdict(list)
    tx_counts: dict[str, int] = defaultdict(int)
    for week_index in range(4):
        window_end = anchor - timedelta(days=7 * week_index)
        window_start = window_end - timedelta(days=6)
        summary = api.get_spending_summary(window_start.isoformat(), window_end.isoformat())
        for m in summary.top_merchants:
            if m.total_spent_cents <= 0:
                continue
            key = normalize_merchant(m.merchant) or m.merchant.strip()
            if not key:
                continue
            week_hits[key].add(week_index)
            week_totals[key].append(m.total_spent_cents)
            tx_counts[key] += m.transaction_count

    proposals: list[MemoryProposal] = []
    for merchant in sorted(week_hits.keys()):
        hits = week_hits[merchant]
        if len(hits) < 3:
            continue
        sorted_weeks = sorted(hits)
        gaps = [
            sorted_weeks[i + 1] - sorted_weeks[i] for i in range(len(sorted_weeks) - 1)
        ]
        mean_gap = sum(gaps) / len(gaps) if gaps else 0.0
        if mean_gap <= 1.15:
            cadence = "weekly"
        elif mean_gap <= 2.25:
            cadence = "biweekly"
        else:
            cadence = "monthly"
        typical = int(median(week_totals[merchant]))
        obs = tx_counts[merchant]
        if obs >= 6:
            confidence = 0.9
        elif obs == 5:
            confidence = 0.8
        elif obs == 4:
            confidence = 0.72
        else:
            confidence = 0.65
        subject = slugify(merchant)
        proposals.append(
            MemoryProposal(
                memory_type="recurring_merchant",
                scope="finance",
                subject=subject,
                confidence=min(confidence, 0.95),
                payload={
                    "cadence": cadence,
                    "typical_amount_cents": typical,
                    "merchant_display": merchant,
                    "weeks_present": len(hits),
                },
                source="detector:recurring_merchant",
                reason=(
                    f"Merchant spend in {len(hits)} of the last four weeks with ~{cadence} spacing."
                ),
            )
        )
    return DetectorResult((), tuple(proposals))


def detect_category_preference(read_models: ReadModels) -> DetectorResult:
    api = read_models.finance_api
    if api is None:
        return DetectorResult.empty()
    anchor = date.fromisoformat(read_models.spending.date)
    start = (anchor - timedelta(days=29)).isoformat()
    end = read_models.spending.date
    summary = api.get_spending_summary(start, end)
    by_cat = {c.category_name: c.total_spent_cents for c in summary.by_category}
    total = sum(by_cat.values())
    if total <= 0:
        return DetectorResult.empty()
    dominant_name, dominant_cents = max(by_cat.items(), key=lambda item: (item[1], item[0]))
    share = dominant_cents / total
    if share < 0.4:
        return DetectorResult.empty()
    confidence = 0.72 + (share - 0.4) * (0.88 - 0.72) / 0.6
    return DetectorResult(
        (),
        (
            MemoryProposal(
                memory_type="category_preference",
                scope="finance",
                subject=slugify(dominant_name),
                confidence=min(float(confidence), 0.92),
                payload={
                    "category_name": dominant_name,
                    "share_of_outflow": round(share, 4),
                    "window_days": 30,
                },
                source="detector:category_preference",
                reason=f"{dominant_name} represents {share * 100:.0f}% of outflow over 30 days.",
            ),
        ),
    )


def detect_schedule_pattern(read_models: ReadModels) -> DetectorResult:
    meals_api = read_models.meals_api
    if meals_api is None:
        return DetectorResult.empty()
    anchor = date.fromisoformat(read_models.spending.date)
    weekday_hits = [0] * 7
    weekday_slots = [0] * 7
    for i in range(28):
        d = anchor - timedelta(days=i)
        wd = d.weekday()
        weekday_slots[wd] += 1
        summary = meals_api.get_nutrition_summary(d.isoformat())
        if summary.meal_count >= 2:
            weekday_hits[wd] += 1

    best_wd: int | None = None
    best_ratio = 0.0
    best_hits = 0
    for wd in range(7):
        slots = weekday_slots[wd]
        if slots == 0:
            continue
        hits = weekday_hits[wd]
        ratio = hits / slots
        is_better = hits > best_hits or (
            hits == best_hits
            and (ratio > best_ratio or (ratio == best_ratio and (best_wd is None or wd < best_wd)))
        )
        if hits >= 3 and ratio >= 0.75 and is_better:
            best_wd = wd
            best_ratio = ratio
            best_hits = hits
    if best_wd is None:
        return DetectorResult.empty()
    name = _WEEKDAY_NAMES[best_wd]
    confidence = min(0.9, 0.78 + 0.03 * (best_hits - 3))
    return DetectorResult(
        (),
        (
            MemoryProposal(
                memory_type="schedule_pattern",
                scope="meals",
                subject=f"{name}_multi_meal",
                confidence=confidence,
                payload={
                    "weekday": name,
                    "hits_in_window": best_hits,
                    "hit_rate": round(best_ratio, 3),
                    "window_days": 28,
                },
                source="detector:schedule_pattern",
                reason=(
                    f"At least two meals logged on {name} on {best_hits} of the last "
                    f"{weekday_slots[best_wd]} {name}s in the 28-day window."
                ),
            ),
        ),
    )


__all__ = [
    "detect_category_preference",
    "detect_recurring_merchant_pattern",
    "detect_schedule_pattern",
]
