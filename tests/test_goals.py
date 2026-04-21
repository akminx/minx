from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.goals import GoalService
from minx_mcp.core.models import GoalCreateInput, GoalUpdateInput
from minx_mcp.db import get_connection


def _make_valid_input(**overrides) -> GoalCreateInput:
    defaults = {
        "goal_type": "spending_cap",
        "title": "Dining out under $250",
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
    }
    defaults.update(overrides)
    return GoalCreateInput(**defaults)


def test_goal_service_create_get_update_archive_round_trip(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    created = service.create_goal(_make_valid_input(notes="March goal"))
    fetched = service.get_goal(created.id)
    updated = service.update_goal(
        created.id, GoalUpdateInput(title="Dining out under $200", target_value=20_000)
    )
    archived = service.archive_goal(created.id)

    assert fetched.id == created.id
    assert fetched.title == "Dining out under $250"
    assert fetched.category_names == ["Dining Out"]
    assert updated.title == "Dining out under $200"
    assert updated.target_value == 20_000
    assert archived.status == "archived"


def test_goal_service_list_goals_filters_by_status(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)
    service.create_goal(_make_valid_input(title="Goal A"))
    service.create_goal(_make_valid_input(title="Goal B"))
    goal_c = service.create_goal(_make_valid_input(title="Goal C"))
    service.archive_goal(goal_c.id)

    active = service.list_goals(status="active")
    archived = service.list_goals(status="archived")
    all_goals = service.list_goals()

    assert [g.title for g in active] == ["Goal A", "Goal B"]
    assert [g.title for g in archived] == ["Goal C"]
    assert [g.title for g in all_goals] == ["Goal A", "Goal B", "Goal C"]


def test_goal_service_list_goals_rejects_invalid_status(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="Invalid status: bogus"):
        service.list_goals(status="bogus")


def test_goal_service_list_goals_rejects_empty_string_status(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="status must be non-empty when provided"):
        service.list_goals(status="")


def test_goal_service_list_goals_returns_all_when_no_status(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)
    service.create_goal(_make_valid_input(title="Goal A"))
    archived = service.create_goal(_make_valid_input(title="Goal B"))
    service.archive_goal(archived.id)

    listed = service.list_goals()

    assert [goal.title for goal in listed] == ["Goal A", "Goal B"]


def test_goal_service_list_active_goals_respects_date_window(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)
    service.create_goal(
        _make_valid_input(title="March", starts_on="2026-03-01", ends_on="2026-03-31")
    )
    service.create_goal(_make_valid_input(title="April", starts_on="2026-04-01", ends_on=None))
    service.create_goal(
        _make_valid_input(title="Feb", starts_on="2026-02-01", ends_on="2026-02-28")
    )

    active_march = service.list_active_goals("2026-03-15")
    active_april = service.list_active_goals("2026-04-15")

    assert [g.title for g in active_march] == ["March"]
    assert [g.title for g in active_april] == ["April"]


def test_goal_service_rejects_goal_without_any_finance_filter(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="at least one finance filter"):
        service.create_goal(
            _make_valid_input(category_names=[], merchant_names=[], account_names=[])
        )


def test_goal_service_rejects_invalid_metric_type(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="metric_type"):
        service.create_goal(_make_valid_input(metric_type="invalid"))


def test_goal_service_rejects_blank_goal_type(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="goal_type must be non-empty"):
        service.create_goal(_make_valid_input(goal_type=""))


def test_goal_service_rejects_invalid_period(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="period"):
        service.create_goal(_make_valid_input(period="yearly"))


def test_goal_service_rejects_invalid_domain(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="domain"):
        service.create_goal(_make_valid_input(domain="health"))


def test_goal_service_rejects_blank_title(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="title"):
        service.create_goal(_make_valid_input(title="   "))


def test_goal_service_rejects_non_positive_target_value(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="target_value"):
        service.create_goal(_make_valid_input(target_value=0))


def test_goal_service_rejects_invalid_starts_on(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="starts_on"):
        service.create_goal(_make_valid_input(starts_on="2026-13-01"))


def test_goal_service_rejects_invalid_ends_on(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="ends_on"):
        service.create_goal(_make_valid_input(ends_on="2026-03-99"))


def test_goal_service_rejects_ends_on_before_starts_on(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="ends_on"):
        service.create_goal(_make_valid_input(starts_on="2026-03-10", ends_on="2026-03-09"))


def test_goal_service_get_nonexistent_goal_raises(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(NotFoundError, match="not found"):
        service.get_goal(999)


def test_goal_service_update_with_no_changes_returns_existing(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)
    created = service.create_goal(_make_valid_input())

    updated = service.update_goal(created.id, GoalUpdateInput())

    assert updated.title == created.title
    assert updated.target_value == created.target_value


def test_goal_service_update_can_clear_notes(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)
    created = service.create_goal(_make_valid_input(notes="keep me"))

    updated = service.update_goal(
        created.id,
        GoalUpdateInput(clear_notes=True),
    )

    assert updated.notes is None


def test_goal_service_update_can_clear_ends_on(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)
    created = service.create_goal(_make_valid_input(ends_on="2026-03-31"))

    updated = service.update_goal(
        created.id,
        GoalUpdateInput(clear_ends_on=True),
    )

    assert updated.ends_on is None


def test_goal_service_rejects_blank_category_name(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="category_names must not contain blank entries"):
        service.create_goal(_make_valid_input(category_names=["  "]))


def test_goal_service_rejects_blank_merchant_name(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="merchant_names must not contain blank entries"):
        service.create_goal(
            _make_valid_input(
                category_names=[],
                merchant_names=[""],
                account_names=["Checking"],
            )
        )


def test_goal_service_rejects_blank_account_name(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="account_names must not contain blank entries"):
        service.create_goal(
            _make_valid_input(
                category_names=[],
                merchant_names=[],
                account_names=["\t"],
            )
        )


def test_goal_service_normalizes_filter_names_on_create(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    created = service.create_goal(_make_valid_input(category_names=["  Dining Out  "]))

    assert created.category_names == ["Dining Out"]


def test_goal_service_rejects_mixed_blank_and_valid_filter_members(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="category_names must not contain blank entries"):
        service.create_goal(_make_valid_input(category_names=["Dining Out", "  "]))
