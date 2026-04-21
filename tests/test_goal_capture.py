from __future__ import annotations

import asyncio

from minx_mcp.core.goal_parse import (
    _resolve_exact_subject,
    capture_goal_message,
)
from minx_mcp.core.models import GoalRecord
from minx_mcp.db import get_connection
from minx_mcp.finance.read_api import FinanceReadAPI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _make_db_and_api(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    # account_id=1 (DCU) and batch_id seeded by migration defaults
    conn.execute(
        "INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint) "
        "VALUES (1, 1, 'csv', 'seed.csv', 'seed')"
    )
    conn.commit()
    return conn, FinanceReadAPI(conn)


def _insert_transaction(
    conn, *, merchant: str, amount_cents: int = -1000, category_id: int | None = None
) -> None:
    conn.execute(
        """
        INSERT INTO finance_transactions
            (account_id, batch_id, posted_at, description, merchant, amount_cents, category_source)
        VALUES (1, 1, '2026-03-15', ?, ?, ?, 'manual')
        """,
        (f"Transaction at {merchant}", merchant, amount_cents),
    )
    conn.commit()


def _goal_record(**overrides) -> GoalRecord:
    defaults = {
        "id": 1,
        "goal_type": "spending_cap",
        "title": "Dining Out Spending Cap",
        "status": "active",
        "metric_type": "sum_below",
        "target_value": 25_000,
        "period": "monthly",
        "domain": "finance",
        "category_names": ["Dining Out"],
        "merchant_names": [],
        "account_names": [],
        "starts_on": "2026-03-01",
        "ends_on": None,
        "notes": None,
        "created_at": "2026-03-01 00:00:00",
        "updated_at": "2026-03-01 00:00:00",
    }
    defaults.update(overrides)
    return GoalRecord(**defaults)


# ---------------------------------------------------------------------------
# _resolve_exact_subject unit tests
# ---------------------------------------------------------------------------


def test_resolve_exact_subject_category_case_insensitive(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)
    # "Dining Out" is seeded by the migrations

    result = _resolve_exact_subject("category", "dining out", api)

    assert result == "Dining Out"


def test_resolve_exact_subject_merchant_via_sq_prefix_normalization(tmp_path):
    conn, api = _make_db_and_api(tmp_path)
    _insert_transaction(conn, merchant="Joe's Cafe")

    result = _resolve_exact_subject("merchant", "SQ *JOES CAFE 1234", api)

    assert result == "Joe's Cafe"


def test_resolve_exact_subject_returns_none_for_nonexistent(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)

    result = _resolve_exact_subject("category", "nonexistent", api)

    assert result is None


# ---------------------------------------------------------------------------
# Create path (regex-based, no LLM)
# ---------------------------------------------------------------------------


def test_capture_goal_message_create_with_known_category(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)
    # "Dining Out" is seeded by migrations

    result = _run(
        capture_goal_message(
            message="spend less than $200 on Dining Out monthly",
            review_date="2026-03-15",
            finance_api=api,
            goals=[],
            llm=None,
        )
    )

    assert result.result_type == "create"
    assert result.action == "goal_create"
    assert result.payload is not None
    assert result.payload["category_names"] == ["Dining Out"]
    assert result.payload["target_value"] == 20000
    assert result.payload["period"] == "monthly"


def test_capture_goal_message_create_weekly_cap_on_known_merchant(tmp_path):
    conn, api = _make_db_and_api(tmp_path)
    _insert_transaction(conn, merchant="Coffee Shop")

    result = _run(
        capture_goal_message(
            message="spend less than $50 on Coffee Shop weekly",
            review_date="2026-03-15",
            finance_api=api,
            goals=[],
            llm=None,
        )
    )

    assert result.result_type == "create"
    assert result.payload is not None
    assert result.payload["period"] == "weekly"
    assert result.payload["target_value"] == 5000
    assert result.payload["merchant_names"] == ["Coffee Shop"]


def test_capture_goal_message_create_ambiguous_subject(tmp_path):
    conn, api = _make_db_and_api(tmp_path)
    # "Dining Out" is in categories; also add it as a merchant
    _insert_transaction(conn, merchant="Dining Out")

    result = _run(
        capture_goal_message(
            message="spend less than $100 on Dining Out monthly",
            review_date="2026-03-15",
            finance_api=api,
            goals=[],
            llm=None,
        )
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "ambiguous_subject"
    assert result.options is not None
    assert len(result.options) == 2


def test_capture_goal_message_create_subject_matches_nothing(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)

    result = _run(
        capture_goal_message(
            message="spend less than $100 on ZorgBlorp monthly",
            review_date="2026-03-15",
            finance_api=api,
            goals=[],
            llm=None,
        )
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "vague_intent"


def test_capture_goal_message_create_no_dollar_amount(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)

    result = _run(
        capture_goal_message(
            message="spend less on Groceries monthly",
            review_date="2026-03-15",
            finance_api=api,
            goals=[],
            llm=None,
        )
    )

    assert result.result_type == "no_match"


def test_capture_goal_message_no_goal_intent(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)

    result = _run(
        capture_goal_message(
            message="what is the weather today",
            review_date="2026-03-15",
            finance_api=api,
            goals=[],
            llm=None,
        )
    )

    assert result.result_type == "no_match"


# ---------------------------------------------------------------------------
# Update path (regex-based, no LLM)
# ---------------------------------------------------------------------------


def test_capture_goal_message_pause_single_matching_goal(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)
    goal = _goal_record(id=7, category_names=["Dining Out"])

    result = _run(
        capture_goal_message(
            message="pause my Dining Out goal",
            review_date="2026-03-15",
            finance_api=api,
            goals=[goal],
            llm=None,
        )
    )

    assert result.result_type == "update"
    assert result.payload == {"status": "paused"}
    assert result.goal_id == 7


def test_capture_goal_message_pause_ambiguous_goals(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)
    goal_a = _goal_record(id=1, title="Dining Out Spending Cap", category_names=["Dining Out"])
    goal_b = _goal_record(id=2, title="Dining Out Spending Cap", category_names=["Dining Out"])

    result = _run(
        capture_goal_message(
            message="pause my Dining Out goal",
            review_date="2026-03-15",
            finance_api=api,
            goals=[goal_a, goal_b],
            llm=None,
        )
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "ambiguous_goal"


def test_capture_goal_message_pause_no_matching_goals(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)

    result = _run(
        capture_goal_message(
            message="pause my Dining Out goal",
            review_date="2026-03-15",
            finance_api=api,
            goals=[],
            llm=None,
        )
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "missing_goal"


def test_capture_goal_message_retarget_with_amount(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)
    goal = _goal_record(id=7, category_names=["Dining Out"])

    result = _run(
        capture_goal_message(
            message="retarget my Dining Out goal to $75",
            review_date="2026-03-15",
            finance_api=api,
            goals=[goal],
            llm=None,
        )
    )

    assert result.result_type == "update"
    assert result.payload is not None
    assert result.payload["target_value"] == 7500


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_capture_goal_message_unsupported_goal_type_only(tmp_path):
    _conn, api = _make_db_and_api(tmp_path)
    goal = _goal_record(
        id=7,
        goal_type="savings_goal",
        metric_type="sum_above",
        category_names=["Dining Out"],
    )

    result = _run(
        capture_goal_message(
            message="pause my Dining Out goal",
            review_date="2026-03-15",
            finance_api=api,
            goals=[goal],
            llm=None,
        )
    )

    assert result.result_type == "no_match"
