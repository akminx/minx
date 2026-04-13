from __future__ import annotations

from minx_mcp.core.events import emit_event, query_events
from minx_mcp.core.read_models import _summarize_event
from minx_mcp.meals.read_api import MealsReadAPI
from minx_mcp.preferences import set_preference


def test_get_nutrition_summary_with_skipped_meals(db_conn, meals_seeder) -> None:
    meals_seeder.meal_entry(
        occurred_at="2026-04-12T19:00:00Z",
        meal_kind="dinner",
        protein_grams=50.0,
        calories=800,
    )

    summary = MealsReadAPI(db_conn).get_nutrition_summary("2026-04-12")

    assert summary.meal_count == 1
    assert summary.protein_grams == 50.0
    assert summary.calories == 800
    assert summary.last_meal_at == "2026-04-12T19:00:00Z"
    assert "no breakfast logged" in summary.skipped_meal_signals
    assert "no lunch logged" in summary.skipped_meal_signals


def test_get_nutrition_summary_uses_configured_timezone_boundaries(db_conn, meals_seeder) -> None:
    set_preference(db_conn, "core", "timezone", "UTC")
    meals_seeder.meal_entry(
        occurred_at="2026-04-12T23:30:00-05:00",
        meal_kind="dinner",
        protein_grams=35.0,
        calories=650,
    )

    summary = MealsReadAPI(db_conn).get_nutrition_summary("2026-04-13")

    assert summary.meal_count == 1
    assert summary.protein_grams == 35.0
    assert summary.calories == 650


def test_summarize_meals_events(db_conn) -> None:
    emit_event(
        db_conn,
        event_type="meal.logged",
        domain="meals",
        occurred_at="2026-04-12T12:00:00Z",
        entity_ref="meal-1",
        source="test",
        payload={"meal_id": 1, "meal_kind": "lunch", "food_count": 2, "calories": 700},
    )
    emit_event(
        db_conn,
        event_type="nutrition.day_updated",
        domain="meals",
        occurred_at="2026-04-12T23:00:00Z",
        entity_ref="2026-04-12",
        source="test",
        payload={"date": "2026-04-12", "meal_count": 3, "protein_grams": 120.0, "calories": 2100},
    )

    summaries = [_summarize_event(event) for event in query_events(db_conn, domain="meals")]

    assert "lunch" in summaries[0]
    assert "700" in summaries[0]
    assert "3 meals" in summaries[1]
    assert "120" in summaries[1]
