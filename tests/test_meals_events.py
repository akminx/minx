from __future__ import annotations

import subprocess
import sys

import pytest

from minx_mcp.core.events import PAYLOAD_MODELS, UnknownEventTypeError, emit_event


def test_payload_models_include_finance_and_meals_events() -> None:
    assert "finance.transactions_imported" in PAYLOAD_MODELS
    assert "finance.transactions_categorized" in PAYLOAD_MODELS
    assert "finance.report_generated" in PAYLOAD_MODELS
    assert "finance.anomalies_detected" in PAYLOAD_MODELS
    assert "meal.logged" in PAYLOAD_MODELS
    assert "nutrition.day_updated" in PAYLOAD_MODELS


@pytest.mark.parametrize("module_name", ["minx_mcp.finance.events", "minx_mcp.meals.events"])
def test_domain_event_modules_import_directly(module_name: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_emit_unknown_meals_event_fails_loudly(db_conn) -> None:
    with pytest.raises(UnknownEventTypeError):
        emit_event(
            db_conn,
            event_type="meals.nonexistent",
            domain="meals",
            occurred_at="2026-04-12T12:00:00Z",
            entity_ref=None,
            source="tests",
            payload={},
        )


def test_emit_meal_logged_rejects_extra_fields(db_conn) -> None:
    event_id = emit_event(
        db_conn,
        event_type="meal.logged",
        domain="meals",
        occurred_at="2026-04-12T12:30:00Z",
        entity_ref="meal-1",
        source="meals.service",
        payload={
            "meal_id": 1,
            "meal_kind": "lunch",
            "food_count": 3,
            "bogus_field": "should fail",
        },
    )

    assert event_id is None
