from __future__ import annotations

from minx_mcp.core.memory_detectors import (
    detect_category_preference,
    detect_recurring_merchant_pattern,
    detect_schedule_pattern,
)
from minx_mcp.core.read_models import build_read_models
from minx_mcp.db import get_connection


def _seed_finance_tx(conn, *, posted_at: str, merchant: str, cents: int, category: str) -> None:
    category_id = conn.execute(
        "SELECT id FROM finance_categories WHERE name = ?",
        (category,),
    ).fetchone()["id"]
    account_id = conn.execute("SELECT id FROM finance_accounts WHERE name = 'DCU'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO finance_import_batches (account_id, source_type, source_ref, raw_fingerprint)
        VALUES (?, 'csv', 'seed.csv', 'fp')
        """,
        (account_id,),
    )
    bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, ?, ?, 'x', ?, ?, ?, 'manual')
        """,
        (account_id, bid, posted_at, merchant, cents, category_id),
    )


def test_recurring_merchant_happy_path(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    for d in ("2026-04-10", "2026-04-05", "2026-03-28", "2026-03-21"):
        _seed_finance_tx(conn, posted_at=d, merchant="STARBUCKS", cents=-500, category="Dining Out")
    conn.commit()
    conn.close()

    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_recurring_merchant_pattern(rm)
    finally:
        conn.close()
    assert len(result.memory_proposals) == 1
    prop = result.memory_proposals[0]
    assert prop.memory_type == "recurring_merchant"
    assert prop.scope == "finance"
    assert prop.confidence >= 0.65
    assert prop.payload["cadence"] == "weekly"


def test_recurring_merchant_below_threshold(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    _seed_finance_tx(conn, posted_at="2026-04-10", merchant="STARBUCKS", cents=-500, category="Dining Out")
    _seed_finance_tx(conn, posted_at="2026-04-05", merchant="STARBUCKS", cents=-500, category="Dining Out")
    conn.commit()
    conn.close()

    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_recurring_merchant_pattern(rm)
    finally:
        conn.close()
    assert result.memory_proposals == ()


def test_recurring_merchant_single_observation(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    _seed_finance_tx(conn, posted_at="2026-04-14", merchant="COFFEE", cents=-100, category="Dining Out")
    conn.commit()
    conn.close()
    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_recurring_merchant_pattern(rm)
    finally:
        conn.close()
    assert result.memory_proposals == ()


def test_category_preference_happy_path(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    for _ in range(7):
        _seed_finance_tx(conn, posted_at="2026-04-10", merchant="A", cents=-600, category="Groceries")
    _seed_finance_tx(conn, posted_at="2026-04-10", merchant="B", cents=-400, category="Dining Out")
    conn.commit()
    conn.close()

    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_category_preference(rm)
    finally:
        conn.close()
    assert len(result.memory_proposals) == 1
    assert result.memory_proposals[0].memory_type == "category_preference"
    assert result.memory_proposals[0].payload["category_name"] == "Groceries"


def test_category_preference_below_share_threshold(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    _seed_finance_tx(conn, posted_at="2026-04-01", merchant="A", cents=-3500, category="Groceries")
    _seed_finance_tx(conn, posted_at="2026-04-02", merchant="B", cents=-3000, category="Dining Out")
    _seed_finance_tx(conn, posted_at="2026-04-03", merchant="C", cents=-3500, category="Shopping")
    conn.commit()
    conn.close()

    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_category_preference(rm)
    finally:
        conn.close()
    assert result.memory_proposals == ()


def test_category_preference_tie_breaker_stable(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    _seed_finance_tx(conn, posted_at="2026-04-05", merchant="A", cents=-5000, category="Groceries")
    _seed_finance_tx(conn, posted_at="2026-04-06", merchant="B", cents=-5000, category="Shopping")
    conn.commit()
    conn.close()

    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_category_preference(rm)
    finally:
        conn.close()
    assert len(result.memory_proposals) == 1
    assert result.memory_proposals[0].payload["share_of_outflow"] == 0.5
    assert result.memory_proposals[0].payload["category_name"] in {"Groceries", "Shopping"}


def test_schedule_pattern_happy_path(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json, updated_at)
        VALUES ('core', 'timezone', '"UTC"', datetime('now'))
        """
    )
    for day in ("2026-04-15", "2026-04-08", "2026-04-01", "2026-03-25"):
        conn.execute(
            """
            INSERT INTO meals_meal_entries (
                occurred_at, meal_kind, protein_grams, calories, food_items_json, source
            ) VALUES (?, 'lunch', 20.0, 400, '[]', 'manual')
            """,
            (f"{day}T12:00:00Z",),
        )
        conn.execute(
            """
            INSERT INTO meals_meal_entries (
                occurred_at, meal_kind, protein_grams, calories, food_items_json, source
            ) VALUES (?, 'dinner', 25.0, 500, '[]', 'manual')
            """,
            (f"{day}T18:00:00Z",),
        )
    conn.commit()
    conn.close()

    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_schedule_pattern(rm)
    finally:
        conn.close()
    assert len(result.memory_proposals) == 1
    assert result.memory_proposals[0].memory_type == "schedule_pattern"
    assert result.memory_proposals[0].scope == "meals"
    assert "wednesday" in result.memory_proposals[0].subject


def test_schedule_pattern_no_meals(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    conn.commit()
    conn.close()
    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_schedule_pattern(rm)
    finally:
        conn.close()
    assert result.memory_proposals == ()


def test_schedule_pattern_sparse_meals(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json, updated_at)
        VALUES ('core', 'timezone', '"UTC"', datetime('now'))
        """
    )
    conn.execute(
        """
        INSERT INTO meals_meal_entries (
            occurred_at, meal_kind, protein_grams, calories, food_items_json, source
        ) VALUES ('2026-04-15T12:00:00Z', 'lunch', 20.0, 400, '[]', 'manual')
        """,
    )
    conn.commit()
    conn.close()
    conn = get_connection(db_path)
    try:
        rm = build_read_models(conn, "2026-04-15")
        result = detect_schedule_pattern(rm)
    finally:
        conn.close()
    assert result.memory_proposals == ()
