from __future__ import annotations

import logging

import pytest

from minx_mcp.core.models import GoalCaptureResult
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from minx_mcp.preferences import set_preference
from tests.helpers import MinxTestConfig, get_tool
from tests.helpers import call_tool_sync as _call_tool_sync


def test_goal_capture_result_requires_top_level_goal_id_for_updates() -> None:
    with pytest.raises(ValueError, match="goal_id is required"):
        GoalCaptureResult(
            result_type="update",
            action="goal_update",
            payload={"status": "paused"},
        )


def test_goal_parse_tool_supports_structured_create_input(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_create",
            "payload": {
                "goal_type": "spending_cap",
                "title": "Dining Out Spending Cap",
                "metric_type": "sum_below",
                "target_value": 25000,
                "period": "monthly",
                "domain": "finance",
                "category_names": ["Dining Out"],
                "merchant_names": [],
                "account_names": [],
                "starts_on": "2026-03-01",
                "ends_on": None,
                "notes": None,
            },
        },
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"
    assert result["data"]["response_template"] == "goal_parse.create.ready"
    assert result["data"]["response_slots"] == {
        "action": "goal_create",
        "goal_type": "spending_cap",
        "subject": "Dining Out",
        "subject_kind": "category",
        "period": "monthly",
        "target_value": 25000,
    }
    assert result["data"]["payload"]["category_names"] == ["Dining Out"]


def test_goal_parse_tool_rejects_noncanonical_structured_create_input(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_create",
            "payload": {
                "goal_type": "spending_cap",
                "title": "Mystery Spending Cap",
                "metric_type": "sum_below",
                "target_value": 25000,
                "period": "monthly",
                "domain": "finance",
                "category_names": ["Mystery"],
                "merchant_names": [],
                "account_names": [],
                "starts_on": "2026-03-01",
                "ends_on": None,
                "notes": None,
            },
        },
        review_date="2026-03-15",
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_goal_parse_tool_falls_back_when_stored_llm_config_is_invalid(tmp_path, caplog) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    set_preference(conn, "core", "llm_config", {"provider": "missing"})
    conn.close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    with caplog.at_level(logging.WARNING, logger="minx_mcp.core.tools.goals"):
        result = _call_tool_sync(goal_parse, message="what's for lunch?", review_date="2026-03-15")

    assert result["success"] is True
    assert result["data"]["result_type"] == "no_match"
    assert result["data"]["response_template"] == "goal_parse.no_match.unsupported"
    assert "using deterministic parser" in caplog.text
    assert "missing" not in caplog.text


def test_goal_parse_tool_supports_structured_update_input(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO goals (
            goal_type, title, status, metric_type, target_value, period, domain,
            filters_json, starts_on, ends_on, notes, created_at, updated_at
        ) VALUES (
            'spending_cap', 'Dining Out Spending Cap', 'active', 'sum_below', 25000, 'monthly', 'finance',
            '{"category_names":["Dining Out"],"merchant_names":[],"account_names":[]}',
            '2026-03-01', NULL, NULL, datetime('now'), datetime('now')
        )
        """
    )
    conn.commit()
    conn.close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_update",
            "goal_id": 1,
            "payload": {"status": "paused"},
        },
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "update"
    assert result["data"]["goal_id"] == 1
    assert result["data"]["response_template"] == "goal_parse.update.ready"
    assert result["data"]["response_slots"] == {
        "action": "goal_update",
        "goal_id": 1,
        "status": "paused",
    }


def test_goal_parse_tool_rejects_non_object_structured_input(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input=["not", "an", "object"],
        review_date="2026-03-15",
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_goal_parse_tool_rejects_structured_create_with_invalid_value_types(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_create",
            "payload": {
                "goal_type": "spending_cap",
                "title": "Dining Out Spending Cap",
                "metric_type": "sum_below",
                "target_value": "25000",
                "period": "monthly",
                "domain": "finance",
                "category_names": ["Dining Out"],
                "merchant_names": [],
                "account_names": [],
                "starts_on": "2026-03-01",
                "ends_on": None,
                "notes": None,
            },
        },
        review_date="2026-03-15",
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_goal_parse_tool_rejects_structured_update_with_invalid_value_types(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO goals (
            goal_type, title, status, metric_type, target_value, period, domain,
            filters_json, starts_on, ends_on, notes, created_at, updated_at
        ) VALUES (
            'spending_cap', 'Dining Out Spending Cap', 'active', 'sum_below', 25000, 'monthly', 'finance',
            '{"category_names":["Dining Out"],"merchant_names":[],"account_names":[]}',
            '2026-03-01', NULL, NULL, datetime('now'), datetime('now')
        )
        """
    )
    conn.commit()
    conn.close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_update",
            "goal_id": 1,
            "payload": {"target_value": "25000"},
        },
        review_date="2026-03-15",
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_goal_parse_tool_returns_no_match_for_unsupported_structured_create_family(
    tmp_path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_create",
            "payload": {
                "goal_type": "savings_goal",
                "title": "Save More",
                "metric_type": "sum_above",
                "target_value": 25000,
                "period": "monthly",
                "domain": "finance",
                "category_names": ["Dining Out"],
                "merchant_names": [],
                "account_names": [],
                "starts_on": "2026-03-01",
                "ends_on": None,
                "notes": None,
            },
        },
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "no_match"
    assert result["data"]["response_template"] == "goal_parse.no_match.unsupported"
    assert result["data"]["response_slots"] == {"status": "unsupported"}


def test_goal_parse_tool_accepts_merchant_alias_that_normalizes_to_canonical(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint) "
        "VALUES (1, 1, 'csv', 'seed.csv', 'seed')"
    )
    conn.execute(
        """
        INSERT INTO finance_transactions
            (account_id, batch_id, posted_at, description, merchant, amount_cents, category_source)
        VALUES (1, 1, '2026-03-15', 'Coffee', 'Joe''s Cafe', -1000, 'manual')
        """
    )
    conn.commit()
    conn.close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_create",
            "payload": {
                "goal_type": "spending_cap",
                "title": "Joe's Cafe Spending Cap",
                "metric_type": "sum_below",
                "target_value": 10000,
                "period": "monthly",
                "domain": "finance",
                "category_names": [],
                "merchant_names": ["SQ *JOES CAFE 1234"],
                "account_names": [],
                "starts_on": "2026-03-01",
                "ends_on": None,
                "notes": None,
            },
        },
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"


def test_goal_parse_tool_rejects_completely_unknown_merchant(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint) "
        "VALUES (1, 1, 'csv', 'seed.csv', 'seed')"
    )
    conn.execute(
        """
        INSERT INTO finance_transactions
            (account_id, batch_id, posted_at, description, merchant, amount_cents, category_source)
        VALUES (1, 1, '2026-03-15', 'Coffee', 'Joe''s Cafe', -1000, 'manual')
        """
    )
    conn.commit()
    conn.close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_create",
            "payload": {
                "goal_type": "spending_cap",
                "title": "Unknown Spending Cap",
                "metric_type": "sum_below",
                "target_value": 10000,
                "period": "monthly",
                "domain": "finance",
                "category_names": [],
                "merchant_names": ["TOTALLY UNKNOWN MERCHANT XYZ"],
                "account_names": [],
                "starts_on": "2026-03-01",
                "ends_on": None,
                "notes": None,
            },
        },
        review_date="2026-03-15",
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"
    assert "canonical names only" in result["error"]


def test_goal_parse_tool_accepts_exact_canonical_merchant_name(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint) "
        "VALUES (1, 1, 'csv', 'seed.csv', 'seed')"
    )
    conn.execute(
        """
        INSERT INTO finance_transactions
            (account_id, batch_id, posted_at, description, merchant, amount_cents, category_source)
        VALUES (1, 1, '2026-03-15', 'Coffee', 'Joe''s Cafe', -1000, 'manual')
        """
    )
    conn.commit()
    conn.close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_create",
            "payload": {
                "goal_type": "spending_cap",
                "title": "Joe's Cafe Spending Cap",
                "metric_type": "sum_below",
                "target_value": 10000,
                "period": "monthly",
                "domain": "finance",
                "category_names": [],
                "merchant_names": ["Joe's Cafe"],
                "account_names": [],
                "starts_on": "2026-03-01",
                "ends_on": None,
                "notes": None,
            },
        },
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"


def test_goal_parse_structured_create_canonicalizes_noncanonical_merchant(tmp_path) -> None:
    """A non-canonical merchant that resolves via normalization must be rewritten
    to its canonical form in the returned payload — otherwise downstream goal
    storage holds the user's raw text and progress queries miss the merchant.
    """
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint) "
        "VALUES (1, 1, 'csv', 'seed.csv', 'seed')"
    )
    conn.execute(
        """
        INSERT INTO finance_transactions
            (account_id, batch_id, posted_at, description, merchant, amount_cents, category_source)
        VALUES (1, 1, '2026-03-15', 'Joe''s Cafe charge', 'Joe''s Cafe', -1000, 'manual')
        """
    )
    conn.commit()
    conn.close()

    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = get_tool(server, "goal_parse").fn

    result = _call_tool_sync(
        goal_parse,
        structured_input={
            "action": "goal_create",
            "payload": {
                "goal_type": "spending_cap",
                "title": "Coffee Cap",
                "metric_type": "sum_below",
                "target_value": 5000,
                "period": "monthly",
                "domain": "finance",
                "category_names": [],
                "merchant_names": ["SQ *JOES CAFE 1234"],
                "account_names": [],
                "starts_on": "2026-03-01",
                "ends_on": None,
                "notes": None,
            },
        },
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["payload"]["merchant_names"] == ["Joe's Cafe"]


_TestConfig = MinxTestConfig
