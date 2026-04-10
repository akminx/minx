from __future__ import annotations

import asyncio
import inspect

import pytest

from minx_mcp.core.models import GoalCaptureResult, GoalRecord
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection


class _StubFinanceRead:
    def list_goal_category_names(self) -> list[str]:
        return ["Cafe", "Dining Out", "Groceries"]

    def list_spending_merchant_names(self) -> list[str]:
        return ["Amazon", "Cafe", "Netflix"]

    def list_account_names(self) -> list[str]:
        return ["DCU"]


def _call_tool_sync(fn, *args, **kwargs):
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _goal_record(**overrides: object) -> GoalRecord:
    defaults = dict(
        id=7,
        goal_type="spending_cap",
        title="Dining Out Spending Cap",
        status="active",
        metric_type="sum_below",
        target_value=25_000,
        period="monthly",
        domain="finance",
        category_names=["Dining Out"],
        merchant_names=[],
        account_names=[],
        starts_on="2026-03-01",
        ends_on=None,
        notes=None,
        created_at="2026-03-01 00:00:00",
        updated_at="2026-03-01 00:00:00",
    )
    defaults.update(overrides)
    return GoalRecord(**defaults)


def test_goal_capture_result_requires_top_level_goal_id_for_updates() -> None:
    with pytest.raises(ValueError, match="goal_id is required"):
        GoalCaptureResult(
            result_type="update",
            action="goal_update",
            payload={"status": "paused"},
            assistant_message="I can update that goal.",
        )


def test_goal_parse_tool_supports_structured_create_input(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = server._tool_manager.get_tool("goal_parse").fn

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
    assert result["data"]["payload"]["category_names"] == ["Dining Out"]


def test_goal_parse_tool_rejects_noncanonical_structured_create_input(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = server._tool_manager.get_tool("goal_parse").fn

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
    goal_parse = server._tool_manager.get_tool("goal_parse").fn

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


def test_goal_parse_tool_rejects_non_object_structured_input(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = server._tool_manager.get_tool("goal_parse").fn

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
    goal_parse = server._tool_manager.get_tool("goal_parse").fn

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
    goal_parse = server._tool_manager.get_tool("goal_parse").fn

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


def test_goal_parse_tool_returns_no_match_for_unsupported_structured_create_family(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = server._tool_manager.get_tool("goal_parse").fn

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


class _TestConfig:
    def __init__(self, db_path, vault_path) -> None:
        self.db_path = db_path
        self.vault_path = vault_path
