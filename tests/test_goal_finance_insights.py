import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.models import GoalCreateInput, ReviewContext
from minx_mcp.core.goals import GoalService
from minx_mcp.core.review import generate_daily_review
from minx_mcp.db import get_connection
from minx_mcp.finance.read_api import FinanceReadAPI
from minx_mcp.vault_writer import VaultWriter


@pytest.mark.asyncio
async def test_goal_finance_insight_flags_monthly_spending_cap_risk(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    goal_service = GoalService(conn)
    goal_service.create_goal(
        GoalCreateInput(
            goal_type="spending_cap",
            title="Dining cap",
            metric_type="sum_below",
            target_value=10_000,
            period="monthly",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-03-01",
            ends_on=None,
            notes=None,
        )
    )
    dcu_id = conn.execute("SELECT id FROM finance_accounts WHERE name = 'DCU'").fetchone()["id"]
    dining_id = conn.execute("SELECT id FROM finance_categories WHERE name = 'Dining Out'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (dcu_id,),
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, '2026-03-15', 'Dinner', 'Restaurant', -6800, ?, 'manual')
        """,
        (dcu_id, dining_id),
    )
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T12:00:00Z",
        entity_ref="1",
        source="test",
        payload={"transaction_count": 1, "account_name": "DCU", "source_kind": "csv", "total_cents": -6800},
    )
    conn.commit()

    artifact = await generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=FinanceReadAPI(conn),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
    )

    assert any("68%" in insight.summary for insight in artifact.insights)


@pytest.mark.asyncio
async def test_goal_finance_insight_skips_late_period_goals_that_are_still_on_pace(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    goal_service = GoalService(conn)
    goal_service.create_goal(
        GoalCreateInput(
            goal_type="spending_cap",
            title="Dining cap",
            metric_type="sum_below",
            target_value=10_000,
            period="monthly",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-03-01",
            ends_on=None,
            notes=None,
        )
    )
    dcu_id = conn.execute("SELECT id FROM finance_accounts WHERE name = 'DCU'").fetchone()["id"]
    dining_id = conn.execute("SELECT id FROM finance_categories WHERE name = 'Dining Out'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (dcu_id,),
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, '2026-03-30', 'Dinner', 'Restaurant', -6800, ?, 'manual')
        """,
        (dcu_id, dining_id),
    )
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-30T12:00:00Z",
        entity_ref="1",
        source="test",
        payload={"transaction_count": 1, "account_name": "DCU", "source_kind": "csv", "total_cents": -6800},
    )
    conn.commit()

    artifact = await generate_daily_review(
        "2026-03-30",
        ReviewContext(
            db_path=db_path,
            finance_api=FinanceReadAPI(conn),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
    )

    assert not any(insight.insight_type == "finance.goal_risk" for insight in artifact.insights)
