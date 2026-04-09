from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.models import (
    DailyReview,
    DailyTimeline,
    DurabilitySinkFailure,
    GoalCreateInput,
    GoalUpdateInput,
    OpenLoopsSnapshot,
    ReviewDurabilityError,
    SpendingSnapshot,
)
from minx_mcp.core.server import (
    _daily_review,
    _goal_capture,
    _goal_create,
    _goal_get,
    _goal_update,
    create_core_server,
)
from minx_mcp.db import get_connection

# -- Goal tool tests --


def test_core_server_registers_goal_tool_names(tmp_path: Path) -> None:
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    assert server._tool_manager.get_tool("daily_review").name == "daily_review"
    assert server._tool_manager.get_tool("goal_create").name == "goal_create"
    assert server._tool_manager.get_tool("goal_list").name == "goal_list"
    assert server._tool_manager.get_tool("goal_get").name == "goal_get"
    assert server._tool_manager.get_tool("goal_update").name == "goal_update"
    assert server._tool_manager.get_tool("goal_archive").name == "goal_archive"


def test_core_server_registers_goal_capture_tool_name(tmp_path: Path) -> None:
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    assert server._tool_manager.get_tool("goal_capture").name == "goal_capture"


def test_goal_create_and_list_tools_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_list = server._tool_manager.get_tool("goal_list").fn

    created = goal_create(
        title="Dining out under $250",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=25_000,
        period="monthly",
        domain="finance",
        category_names=["Dining Out"],
        merchant_names=[],
        account_names=[],
        starts_on="2026-03-01",
        ends_on=None,
        notes="March goal",
    )
    listed = goal_list(status="active")

    assert created["success"] is True
    assert created["data"]["goal"]["title"] == "Dining out under $250"
    assert listed["success"] is True
    assert [g["title"] for g in listed["data"]["goals"]] == ["Dining out under $250"]


def test_goal_capture_returns_invalid_input_for_blank_message(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(message="   ", review_date="2026-03-15")

    assert result == {
        "success": False,
        "data": None,
        "error": "message must be non-empty after trimming",
        "error_code": "INVALID_INPUT",
    }


def test_goal_capture_returns_create_payload_with_explicit_starts_on(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-20",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"
    assert result["data"]["assistant_message"] is not None
    assert result["data"]["payload"]["starts_on"] == "2026-03-01"


def test_goal_capture_returns_invalid_input_for_overlong_message(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(message="x" * 501, review_date="2026-03-15")

    assert result == {
        "success": False,
        "data": None,
        "error": "message must be at most 500 characters",
        "error_code": "INVALID_INPUT",
    }


def test_goal_capture_returns_invalid_input_for_bad_review_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-99",
    )

    assert result == {
        "success": False,
        "data": None,
        "error": "review_date must be a valid ISO date",
        "error_code": "INVALID_INPUT",
    }


def test_goal_capture_supports_today_phrase_with_daily_period(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(
        message="Make a goal to spend less than $25 on dining out today",
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"
    assert result["data"]["payload"]["period"] == "daily"
    assert result["data"]["payload"]["starts_on"] == "2026-03-15"


def test_goal_capture_honors_explicit_iso_start_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(
        message="Make a goal to spend less than $25 on dining out starting 2026-04-10",
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"
    assert result["data"]["payload"]["starts_on"] == "2026-04-10"


def test_goal_capture_accepts_valid_non_20xx_explicit_start_dates(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    older = goal_capture(
        message="Make a goal to spend less than $25 on dining out starting 1999-04-10",
        review_date="2026-03-15",
    )
    future = goal_capture(
        message="Make a goal to spend less than $25 on dining out starting 2100-04-10",
        review_date="2026-03-15",
    )

    assert older["success"] is True
    assert older["data"]["payload"]["starts_on"] == "1999-04-10"
    assert future["success"] is True
    assert future["data"]["payload"]["starts_on"] == "2100-04-10"


def test_goal_capture_rejects_invalid_explicit_start_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(
        message="Make a goal to spend less than $25 on dining out starting 2026-04-99",
        review_date="2026-03-15",
    )

    assert result == {
        "success": False,
        "data": None,
        "error": "explicit start date must be a valid ISO date",
        "error_code": "INVALID_INPUT",
    }


def test_goal_capture_returns_resolved_resume_payload_for_ambiguous_create_subject(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute("INSERT INTO finance_categories (name) VALUES ('Cafe')")
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fingerprint')
        """
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id,
            batch_id,
            posted_at,
            description,
            merchant,
            amount_cents,
            category_id,
            category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, "2026-03-15", "Coffee", "Cafe", -1200, 3, "manual"),
    )
    conn.commit()
    conn.close()

    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(
        message="Make a goal to spend less than $60 at Cafe this week",
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "clarify"
    assert result["data"]["clarification_type"] == "ambiguous_subject"
    assert result["data"]["action"] == "goal_create"
    assert result["data"]["resume_payload"] == {
        "goal_type": "spending_cap",
        "title": "Cafe Spending Cap",
        "metric_type": "sum_below",
        "target_value": 6_000,
        "period": "weekly",
        "domain": "finance",
        "category_names": [],
        "merchant_names": [],
        "account_names": [],
        "starts_on": "2026-03-09",
        "ends_on": None,
        "notes": None,
    }
    assert result["data"]["options"] == [
        {
            "kind": "category",
            "label": "Cafe",
            "payload_fragment": {
                "title": "Cafe Spending Cap",
                "category_names": ["Cafe"],
            },
        },
        {
            "kind": "merchant",
            "label": "Cafe",
            "payload_fragment": {
                "title": "Cafe Spending Cap",
                "merchant_names": ["Cafe"],
            },
        },
    ]


def test_goal_capture_preserves_distinct_merchant_spelling_in_ambiguous_create_contract(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute("INSERT INTO finance_categories (name) VALUES ('M&M')")
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fingerprint')
        """
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id,
            batch_id,
            posted_at,
            description,
            merchant,
            amount_cents,
            category_id,
            category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, "2026-03-15", "Candy", "M M", -1200, 3, "manual"),
    )
    conn.commit()
    conn.close()

    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(
        message="Make a goal to spend less than $60 at M&M this week",
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "clarify"
    assert result["data"]["clarification_type"] == "ambiguous_subject"
    assert result["data"]["resume_payload"]["title"] == "M&M Spending Cap"
    assert result["data"]["options"] == [
        {
            "kind": "category",
            "label": "M&M",
            "payload_fragment": {
                "title": "M&M Spending Cap",
                "category_names": ["M&M"],
            },
        },
        {
            "kind": "merchant",
            "label": "M M",
            "payload_fragment": {
                "title": "M M Spending Cap",
                "merchant_names": ["M M"],
            },
        },
    ]


def test_goal_capture_returns_ambiguous_goal_contract_for_multiple_matches(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    first_goal = goal_create(
        title="Dining Out Spending Cap",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=25_000,
        period="monthly",
        category_names=["Dining Out"],
        starts_on="2026-03-01",
    )
    second_goal = goal_create(
        title="Dining Out Vacation Spending Cap",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=8_000,
        period="weekly",
        category_names=["Dining Out"],
        starts_on="2026-03-09",
    )

    result = goal_capture(message="Pause my dining out goal", review_date="2026-03-15")

    assert result["success"] is True
    assert result["data"]["result_type"] == "clarify"
    assert result["data"]["clarification_type"] == "ambiguous_goal"
    assert result["data"]["action"] == "goal_update"
    assert result["data"]["question"] == "Which goal do you mean?"
    assert result["data"]["resume_payload"] == {"status": "paused"}
    assert result["data"]["options"] == [
        {
            "goal_id": first_goal["data"]["goal"]["id"],
            "title": "Dining Out Spending Cap",
            "period": "monthly",
            "target_value": 25_000,
            "status": "active",
            "filter_summary": "category_names=['Dining Out']",
        },
        {
            "goal_id": second_goal["data"]["goal"]["id"],
            "title": "Dining Out Vacation Spending Cap",
            "period": "weekly",
            "target_value": 8_000,
            "status": "active",
            "filter_summary": "category_names=['Dining Out']",
        },
    ]


def test_goal_capture_returns_missing_target_for_retarget_without_amount(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    goal_create(
        title="Dining Out Spending Cap",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=25_000,
        period="monthly",
        category_names=["Dining Out"],
        starts_on="2026-03-01",
    )

    result = goal_capture(message="Change my dining out goal", review_date="2026-03-15")

    assert result["success"] is True
    assert result["data"]["result_type"] == "clarify"
    assert result["data"]["clarification_type"] == "missing_target"
    assert result["data"]["action"] == "goal_update"


def test_goal_get_update_archive_tools(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_get = server._tool_manager.get_tool("goal_get").fn
    goal_update = server._tool_manager.get_tool("goal_update").fn
    goal_archive = server._tool_manager.get_tool("goal_archive").fn

    created = goal_create(
        title="Test goal",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="weekly",
        domain="finance",
        category_names=["Groceries"],
        starts_on="2026-03-01",
    )
    goal_id = created["data"]["goal"]["id"]

    fetched = goal_get(goal_id=goal_id, review_date="2026-03-10")
    assert fetched["success"] is True
    assert fetched["data"]["goal"]["title"] == "Test goal"
    assert fetched["data"]["progress"]["goal_id"] == goal_id
    assert fetched["data"]["progress"]["current_start"] == "2026-03-09"
    assert fetched["data"]["progress"]["current_end"] == "2026-03-15"

    updated = goal_update(goal_id=goal_id, title="Updated goal", target_value=15_000)
    assert updated["success"] is True
    assert updated["data"]["goal"]["title"] == "Updated goal"
    assert updated["data"]["goal"]["target_value"] == 15_000

    archived = goal_archive(goal_id=goal_id)
    assert archived["success"] is True
    assert archived["data"]["goal"]["status"] == "archived"


def test_goal_create_returns_error_for_invalid_input(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn

    result = goal_create(
        title="Bad goal",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        domain="finance",
        category_names=[],
        merchant_names=[],
        account_names=[],
    )
    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_goal_create_defaults_domain_to_finance(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn

    created = goal_create(
        title="Groceries under $400",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=40_000,
        period="monthly",
        category_names=["Groceries"],
        starts_on="2026-03-01",
    )

    assert created["success"] is True
    assert created["data"]["goal"]["domain"] == "finance"


def test_goal_create_rejects_empty_goal_type(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn

    result = goal_create(
        title="Bad goal type",
        goal_type="",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        category_names=["Dining Out"],
        starts_on="2026-03-01",
    )

    assert result == {
        "success": False,
        "data": None,
        "error": "goal_type must be non-empty",
        "error_code": "INVALID_INPUT",
    }


def test_goal_create_rejects_empty_string_starts_on(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn

    result = goal_create(
        title="Bad goal",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        domain="finance",
        category_names=["Dining Out"],
        starts_on="",
    )

    assert result == {
        "success": False,
        "data": None,
        "error": "starts_on must be a valid ISO date",
        "error_code": "INVALID_INPUT",
    }


def test_goal_create_omitted_starts_on_defaults_to_today(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn

    created = goal_create(
        title="Bad habit budget",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        category_names=["Dining Out"],
    )

    assert created["success"] is True
    assert created["data"]["goal"]["starts_on"] == date.today().isoformat()


def test_goal_list_rejects_invalid_status_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_list = server._tool_manager.get_tool("goal_list").fn

    result = goal_list(status="bogus")

    assert result == {
        "success": False,
        "data": None,
        "error": "Invalid status: bogus; must be one of ['active', 'archived', 'completed', 'paused']",
        "error_code": "INVALID_INPUT",
    }


def test_goal_list_rejects_empty_string_status_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_list = server._tool_manager.get_tool("goal_list").fn

    result = goal_list(status="")

    assert result == {
        "success": False,
        "data": None,
        "error": "status must be non-empty when provided",
        "error_code": "INVALID_INPUT",
    }


def test_goal_list_defaults_to_active_goals_only(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_list = server._tool_manager.get_tool("goal_list").fn
    goal_archive = server._tool_manager.get_tool("goal_archive").fn

    active = goal_create(
        title="Active goal",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        category_names=["Dining Out"],
        starts_on="2026-03-01",
    )
    archived = goal_create(
        title="Archived goal",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        category_names=["Dining Out"],
        starts_on="2026-03-01",
    )
    goal_archive(goal_id=archived["data"]["goal"]["id"])

    listed = goal_list()

    assert listed["success"] is True
    assert [goal["title"] for goal in listed["data"]["goals"]] == ["Active goal"]


def test_goal_get_returns_null_progress_outside_goal_lifetime(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_get = server._tool_manager.get_tool("goal_get").fn

    created = goal_create(
        title="March groceries",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        domain="finance",
        category_names=["Groceries"],
        starts_on="2026-03-01",
        ends_on="2026-03-31",
    )

    fetched = goal_get(goal_id=created["data"]["goal"]["id"], review_date="2026-04-01")

    assert fetched["success"] is True
    assert fetched["data"]["goal"]["title"] == "March groceries"
    assert fetched["data"]["progress"] is None


def test_goal_get_rejects_empty_string_review_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_get = server._tool_manager.get_tool("goal_get").fn

    created = goal_create(
        title="March groceries",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        category_names=["Groceries"],
        starts_on="2026-03-01",
    )

    result = goal_get(goal_id=created["data"]["goal"]["id"], review_date="")

    assert result == {
        "success": False,
        "data": None,
        "error": "review_date must be a valid ISO date",
        "error_code": "INVALID_INPUT",
    }


def test_goal_get_excludes_future_transactions_through_server_boundary(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fingerprint')
        """
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id,
            batch_id,
            posted_at,
            description,
            merchant,
            amount_cents,
            category_id,
            category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, "2026-03-15", "Lunch", "Cafe", -1200, 3, "manual"),
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id,
            batch_id,
            posted_at,
            description,
            merchant,
            amount_cents,
            category_id,
            category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, "2026-03-20", "Dinner", "Cafe", -2800, 3, "manual"),
    )
    conn.commit()
    conn.close()

    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_get = server._tool_manager.get_tool("goal_get").fn

    created = goal_create(
        title="Dining out under $250",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=25_000,
        period="monthly",
        category_names=["Dining Out"],
        starts_on="2026-03-01",
    )

    fetched = goal_get(goal_id=created["data"]["goal"]["id"], review_date="2026-03-15")

    assert fetched["success"] is True
    assert fetched["data"]["progress"]["actual_value"] == 1200
    assert fetched["data"]["progress"]["summary"] == "On track: $12.00 of $250.00."


def test_goal_update_can_clear_nullable_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_update = server._tool_manager.get_tool("goal_update").fn

    created = goal_create(
        title="Temporary goal",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=10_000,
        period="monthly",
        domain="finance",
        category_names=["Dining Out"],
        starts_on="2026-03-01",
        ends_on="2026-03-31",
        notes="temporary",
    )
    goal_id = created["data"]["goal"]["id"]

    updated = goal_update(
        goal_id=goal_id,
        clear_ends_on=True,
        clear_notes=True,
    )

    assert updated["success"] is True
    assert updated["data"]["goal"]["ends_on"] is None
    assert updated["data"]["goal"]["notes"] is None


def test_goal_capture_repo_e2e_flow_exercises_progress_before_protected_review(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    config = _TestConfig(db_path, tmp_path / "vault")

    create_result = _goal_capture(
        config,
        "Make a goal to spend less than $250 on dining out this month",
        "2026-03-15",
    )
    created_goal = _goal_create(config, GoalCreateInput(**create_result["payload"]))

    conn = get_connection(db_path)
    _seed_matching_dining_transaction(conn, posted_at="2026-03-15", amount_cents=-1200)
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T15:00:00Z",
        entity_ref="batch-1",
        source="tests",
        payload={
            "account_name": "DCU",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 1,
            "total_cents": -1200,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    progress_before_update = _goal_get(config, created_goal["goal"]["id"], "2026-03-15")
    assert progress_before_update["progress"]["actual_value"] == 1200

    update_result = _goal_capture(config, "Pause my dining out goal", "2026-03-15")
    _goal_update(config, update_result["goal_id"], GoalUpdateInput(**update_result["payload"]))

    progress_after_update = _goal_get(config, created_goal["goal"]["id"], "2026-03-15")
    assert progress_after_update["goal"]["status"] == "paused"
    assert progress_after_update["progress"]["actual_value"] == 1200


@pytest.mark.asyncio
async def test_goal_capture_repo_e2e_flow_keeps_protected_review_redacted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    config = _TestConfig(db_path, tmp_path / "vault")

    create_result = _goal_capture(
        config,
        "Make a goal to spend less than $250 on dining out this month",
        "2026-03-15",
    )
    _goal_create(config, GoalCreateInput(**create_result["payload"]))

    conn = get_connection(db_path)
    _seed_matching_dining_transaction(conn, posted_at="2026-03-15", amount_cents=-1200)
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T15:00:00Z",
        entity_ref="batch-1",
        source="tests",
        payload={
            "account_name": "DCU",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 1,
            "total_cents": -1200,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    review = await _daily_review(config, "2026-03-15", False)

    assert review["redaction_applied"] is True
    assert "goal_progress" not in review
    assert "markdown" not in review


def _seed_matching_dining_transaction(
    conn,
    *,
    posted_at: str,
    amount_cents: int,
    merchant: str = "Cafe",
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fingerprint')
        """
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id,
            batch_id,
            posted_at,
            description,
            merchant,
            amount_cents,
            category_id,
            category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, posted_at, "Seed dining transaction", merchant, amount_cents, 3, "manual"),
    )


class _TestConfig:
    def __init__(self, db_path: Path, vault_path: Path) -> None:
        self._db_path = db_path
        self._vault_path = vault_path

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def vault_path(self) -> Path:
        return self._vault_path


@pytest.mark.asyncio
async def test_daily_review_tool_returns_structured_result(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T15:00:00Z",
        entity_ref="batch-1",
        source="tests",
        payload={
            "account_name": "Checking",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 3,
            "total_cents": -6000,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    vault_path = tmp_path / "vault"
    config = _TestConfig(db_path, vault_path)

    result = await _daily_review(config, "2026-03-15", False)

    assert result["date"] == "2026-03-15"
    assert isinstance(result["narrative"], str)
    assert isinstance(result["next_day_focus"], list)
    assert isinstance(result["attention_areas"], list)
    assert result["activity_level"] in {"none", "low", "moderate", "high"}
    assert result["goal_attention_level"] in {"none", "some", "many"}
    assert result["open_loop_level"] in {"none", "some", "many"}
    assert result["llm_enriched"] is False
    assert result["redaction_applied"] is True
    assert result["redaction_policy"] == "core_default_v1"
    assert "timeline" not in result
    assert "spending" not in result
    assert "open_loops" not in result
    assert "insights" not in result
    assert "goal_progress" not in result
    assert "markdown" not in result

    note_path = vault_path / "Minx" / "Reviews" / "2026-03-15-daily-review.md"
    assert note_path.exists()


@pytest.mark.asyncio
async def test_daily_review_tool_returns_protected_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T15:00:00Z",
        entity_ref="batch-1",
        source="tests",
        payload={
            "account_name": "Checking",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 3,
            "total_cents": -6000,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    result = await _daily_review(_TestConfig(db_path, tmp_path / "vault"), "2026-03-15", False)

    assert result["date"] == "2026-03-15"
    assert result["redaction_applied"] is True
    assert result["redaction_policy"] == "core_default_v1"
    assert "timeline" not in result
    assert "spending" not in result
    assert "goal_progress" not in result
    assert "insights" not in result
    assert "markdown" not in result
    assert isinstance(result["attention_areas"], list)
    assert result["activity_level"] in {"none", "low", "moderate", "high"}
    assert "blocked_fields" in result


@pytest.mark.asyncio
async def test_daily_review_contract_wraps_protected_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    daily_review = server._tool_manager.get_tool("daily_review").fn

    result = await daily_review("2026-03-15", False)

    assert result["success"] is True
    assert result["data"]["redaction_applied"] is True
    assert "blocked_fields" in result["data"]
    assert "markdown" not in result["data"]


@pytest.mark.asyncio
async def test_daily_review_tool_validates_bad_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    config = _TestConfig(db_path, tmp_path / "vault")

    from minx_mcp.contracts import InvalidInputError

    with pytest.raises(InvalidInputError, match="valid ISO date"):
        await _daily_review(config, "not-a-date", False)


@pytest.mark.asyncio
async def test_daily_review_tool_defaults_to_today(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    config = _TestConfig(db_path, tmp_path / "vault")

    result = await _daily_review(config, None, False)
    assert result["date"] is not None
    assert isinstance(result["date"], str)


@pytest.mark.asyncio
async def test_daily_review_tool_function_is_awaitable_and_returns_contract_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    daily_review = server._tool_manager.get_tool("daily_review").fn

    result = await daily_review("2026-03-15", False)

    assert result["success"] is True
    assert result["data"]["date"] == "2026-03-15"
    assert result["data"]["redaction_applied"] is True
    assert "markdown" not in result["data"]


@pytest.mark.asyncio
async def test_daily_review_tool_returns_invalid_input_contract_for_bad_date(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    daily_review = server._tool_manager.get_tool("daily_review").fn

    result = await daily_review("not-a-date", False)

    assert result == {
        "success": False,
        "data": None,
        "error": "review_date must be a valid ISO date",
        "error_code": "INVALID_INPUT",
    }


@pytest.mark.asyncio
async def test_daily_review_tool_rejects_empty_string_review_date(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    daily_review = server._tool_manager.get_tool("daily_review").fn

    result = await daily_review("", False)

    assert result == {
        "success": False,
        "data": None,
        "error": "review_date must be a valid ISO date",
        "error_code": "INVALID_INPUT",
    }


@pytest.mark.asyncio
async def test_daily_review_contract_surfaces_protected_artifact_on_durability_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    daily_review = server._tool_manager.get_tool("daily_review").fn

    async def _raise_durability_error(review_date, ctx, force=False):
        raise ReviewDurabilityError(
            DailyReview(
                date=review_date,
                timeline=DailyTimeline(date=review_date, entries=[]),
                spending=SpendingSnapshot(
                    date=review_date,
                    total_spent_cents=0,
                    by_category={},
                    top_merchants=[],
                    vs_prior_week_pct=None,
                    uncategorized_count=0,
                    uncategorized_total_cents=0,
                ),
                open_loops=OpenLoopsSnapshot(date=review_date, loops=[]),
                goal_progress=[],
                insights=[],
                narrative="raw internal narrative",
                next_day_focus=[],
                llm_enriched=False,
            ),
            [DurabilitySinkFailure("vault_note", OSError("disk full"))],
        )

    monkeypatch.setattr("minx_mcp.core.server.generate_daily_review", _raise_durability_error)

    result = await daily_review("2026-03-15", False)

    assert result["success"] is False
    assert result["error_code"] == "CONFLICT"
    assert result["data"]["date"] == "2026-03-15"
    assert result["data"]["redaction_applied"] is True
    assert result["data"]["recoverable"] is True
    assert result["data"]["durability_failures"] == [
        {"sink": "vault_note", "error": "disk full"}
    ]
    assert "raw internal narrative" not in str(result["data"])
