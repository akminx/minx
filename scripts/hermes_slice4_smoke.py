from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from minx_mcp.core.models import SnapshotContext
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.meals.service import MealsService
from minx_mcp.training.service import TrainingService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed Slice 4 training+nutrition data and print combined snapshot signals"
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--review-date", default="2026-04-13")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Write seed data directly to --db-path (default uses a temporary copy).",
    )
    return parser


async def run_smoke(db_path: Path, review_date: str) -> dict[str, object]:
    review = date.fromisoformat(review_date)
    prior = (review - timedelta(days=1)).isoformat()
    review_day = review.isoformat()
    training = TrainingService(db_path)
    with training:
        deadlift = training.upsert_exercise(display_name="Deadlift", is_compound=True)
        training.log_session(
            occurred_at=f"{prior}T08:00:00Z",
            sets=[{"exercise_id": deadlift.id, "reps": 5, "weight_kg": 140.0}],
        )
        training.log_session(
            occurred_at=f"{review_day}T08:00:00Z",
            sets=[{"exercise_id": deadlift.id, "reps": 5, "weight_kg": 145.0}],
        )

    meals = MealsService(db_path)
    with meals:
        meals.log_meal(
            occurred_at=f"{review_day}T12:00:00Z",
            meal_kind="lunch",
            protein_grams=20.0,
            calories=900,
            summary="smoke meal",
        )

    snapshot = await build_daily_snapshot(
        review_date,
        SnapshotContext(db_path=db_path),
    )

    return {
        "date": snapshot.date,
        "training": asdict(snapshot.training) if snapshot.training is not None else None,
        "nutrition": asdict(snapshot.nutrition) if snapshot.nutrition is not None else None,
        "signal_types": [signal.insight_type for signal in snapshot.signals],
        "attention_items": snapshot.attention_items,
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    source_db = Path(args.db_path).expanduser().resolve()
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    run_db = source_db
    if not args.in_place:
        temp_dir = tempfile.TemporaryDirectory(prefix="minx-slice4-smoke-")
        run_db = Path(temp_dir.name) / source_db.name
        if source_db.exists():
            shutil.copy2(source_db, run_db)
    try:
        payload = asyncio.run(run_smoke(run_db, str(args.review_date)))
    except ValueError as exc:
        parser.error(f"--review-date must be a valid ISO date: {exc}")
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
    payload["source_db_path"] = str(source_db)
    payload["run_db_path"] = str(run_db) if args.in_place else None
    payload["used_temporary_copy"] = not bool(args.in_place)
    payload["in_place"] = bool(args.in_place)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
