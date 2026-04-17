from __future__ import annotations

import json

from minx_mcp.core.events import PAYLOAD_UPCASTERS, emit_event, query_events
from minx_mcp.db import get_connection
from minx_mcp.finance.read_api import FinanceReadAPI

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_batch(conn) -> None:
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fp')
        """
    )


def _insert_transaction(conn, *, posted_at, amount_cents, merchant="Merchant", description="Txn"):
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_source
        )
        VALUES (1, 1, ?, ?, ?, ?, 'manual')
        """,
        (posted_at, description, merchant, amount_cents),
    )


def _imported_payload(**overrides):
    base = {
        "account_name": "Checking",
        "account_id": 1,
        "job_id": "job-1",
        "transaction_count": 1,
        "total_cents": 100,
        "source_kind": "csv",
    }
    base.update(overrides)
    return base


def _insert_raw_event(
    conn,
    *,
    event_type,
    domain,
    occurred_at,
    payload=None,
    schema_version=1,
    sensitivity="normal",
):
    payload_json = json.dumps(payload or {})
    cursor = conn.execute(
        """
        INSERT INTO events (
            event_type, domain, occurred_at, recorded_at,
            entity_ref, source, payload, schema_version, sensitivity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            domain,
            occurred_at,
            "2026-01-01T00:00:00Z",
            None,
            "tests",
            payload_json,
            schema_version,
            sensitivity,
        ),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Income / Net Flow tests
# ---------------------------------------------------------------------------


def test_get_income_summary_counts_only_positive_amounts(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-01",
        amount_cents=500_00,
        merchant="Employer",
        description="Payroll",
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-05",
        amount_cents=100_00,
        merchant="Side Hustle",
        description="Freelance",
    )
    _insert_transaction(
        conn, posted_at="2026-03-10", amount_cents=-75_00, merchant="Grocery", description="Food"
    )
    conn.commit()

    summary = FinanceReadAPI(conn).get_income_summary("2026-03-01", "2026-03-31")

    assert summary.total_income_cents == 600_00
    sources = {s.name: s.total_cents for s in summary.by_source}
    assert sources == {"Employer": 500_00, "Side Hustle": 100_00}


def test_get_income_summary_returns_zero_when_no_income(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn, posted_at="2026-03-01", amount_cents=-200_00, merchant="Shop", description="Stuff"
    )
    conn.commit()

    summary = FinanceReadAPI(conn).get_income_summary("2026-03-01", "2026-03-31")

    assert summary.total_income_cents == 0
    assert summary.by_source == []


def test_get_net_flow_returns_income_minus_expenses(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-01",
        amount_cents=300_00,
        merchant="Employer",
        description="Payroll",
    )
    _insert_transaction(
        conn, posted_at="2026-03-05", amount_cents=-120_00, merchant="Grocery", description="Food"
    )
    _insert_transaction(
        conn, posted_at="2026-03-10", amount_cents=-30_00, merchant="Coffee", description="Coffee"
    )
    conn.commit()

    net = FinanceReadAPI(conn).get_net_flow("2026-03-01", "2026-03-31")

    assert net == 300_00 - 120_00 - 30_00


def test_get_income_summary_date_range_excludes_out_of_range_transactions(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-02-28",
        amount_cents=200_00,
        merchant="OldPayroll",
        description="Before range",
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        amount_cents=500_00,
        merchant="Payroll",
        description="In range",
    )
    _insert_transaction(
        conn,
        posted_at="2026-04-01",
        amount_cents=200_00,
        merchant="FuturePayroll",
        description="After range",
    )
    conn.commit()

    summary = FinanceReadAPI(conn).get_income_summary("2026-03-01", "2026-03-31")

    assert summary.total_income_cents == 500_00
    assert len(summary.by_source) == 1
    assert summary.by_source[0].name == "Payroll"


def test_get_net_flow_date_range_excludes_out_of_range_transactions(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-02-28",
        amount_cents=-999_00,
        merchant="OldSpend",
        description="Before range",
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-10",
        amount_cents=400_00,
        merchant="Employer",
        description="In range income",
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-20",
        amount_cents=-100_00,
        merchant="Shop",
        description="In range expense",
    )
    _insert_transaction(
        conn,
        posted_at="2026-04-05",
        amount_cents=999_00,
        merchant="FutureIncome",
        description="After range",
    )
    conn.commit()

    net = FinanceReadAPI(conn).get_net_flow("2026-03-01", "2026-03-31")

    assert net == 400_00 - 100_00


# ---------------------------------------------------------------------------
# Sensitivity filter tests
# ---------------------------------------------------------------------------


def test_query_events_with_sensitivity_excludes_other_sensitivity_values(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    normal_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-01T12:00:00Z",
        entity_ref=None,
        source="tests",
        payload=_imported_payload(),
        sensitivity="normal",
    )
    _insert_raw_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-01T13:00:00Z",
        payload=_imported_payload(),
        sensitivity="private",
    )

    events = query_events(conn, sensitivity="normal")

    assert [e.id for e in events] == [normal_id]
    assert all(e.sensitivity == "normal" for e in events)


def test_query_events_without_sensitivity_returns_all_events(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    normal_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-01T12:00:00Z",
        entity_ref=None,
        source="tests",
        payload=_imported_payload(),
        sensitivity="normal",
    )
    private_id = _insert_raw_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-01T13:00:00Z",
        payload=_imported_payload(),
        sensitivity="private",
    )

    events = query_events(conn)

    ids = [e.id for e in events]
    assert normal_id in ids
    assert private_id in ids


def test_query_events_sensitivity_filter_is_exact_match_not_substring(tmp_path):
    """The sensitivity filter uses SQL '=' so only the exact value matches.

    The schema enforces NOT NULL DEFAULT 'normal', so we test that filtering by
    one sensitivity value excludes rows with a different sensitivity value, and
    that querying a third sensitivity value returns nothing.
    """
    conn = get_connection(tmp_path / "minx.db")
    normal_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-01T10:00:00Z",
        entity_ref=None,
        source="tests",
        payload=_imported_payload(),
        sensitivity="normal",
    )
    private_id = _insert_raw_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-01T11:00:00Z",
        payload=_imported_payload(),
        sensitivity="private",
    )

    # Filtering for "normal" returns only the normal event
    normal_events = query_events(conn, sensitivity="normal")
    assert [e.id for e in normal_events] == [normal_id]

    # Filtering for "private" returns only the private event
    private_events = query_events(conn, sensitivity="private")
    assert [e.id for e in private_events] == [private_id]

    # Filtering for a value that matches nothing returns an empty list
    none_events = query_events(conn, sensitivity="confidential")
    assert none_events == []


# ---------------------------------------------------------------------------
# Event payload upcasting tests
# ---------------------------------------------------------------------------


def _register_upcaster(event_type, version, fn):
    """Register an upcaster into PAYLOAD_UPCASTERS and return a teardown callable."""
    PAYLOAD_UPCASTERS.setdefault(event_type, {})[version] = fn

    def _cleanup():
        PAYLOAD_UPCASTERS.get(event_type, {}).pop(version, None)

    return _cleanup


def test_upcaster_transforms_old_schema_payload(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    # Insert an event with schema_version=1 that is missing "new_field"
    old_payload = _imported_payload()
    old_payload_json = json.dumps(old_payload)
    cursor = conn.execute(
        """
        INSERT INTO events (
            event_type, domain, occurred_at, recorded_at,
            entity_ref, source, payload, schema_version, sensitivity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "finance.transactions_imported",
            "finance",
            "2026-03-01T12:00:00Z",
            "2026-03-01T12:00:00Z",
            None,
            "tests",
            old_payload_json,
            1,
            "normal",
        ),
    )
    event_id = cursor.lastrowid
    conn.commit()

    # Register upcaster: v1 → add "new_field"
    def add_new_field(p):
        return {**p, "new_field": "added"}

    cleanup = _register_upcaster("finance.transactions_imported", 1, add_new_field)
    try:
        events = query_events(conn, event_type="finance.transactions_imported")
        target = next(e for e in events if e.id == event_id)
        assert target.payload.get("new_field") == "added"
    finally:
        cleanup()


def test_upcasters_chain_v1_to_v3(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    old_payload = {"value": 0}
    cursor = conn.execute(
        """
        INSERT INTO events (
            event_type, domain, occurred_at, recorded_at,
            entity_ref, source, payload, schema_version, sensitivity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "finance.transactions_imported",
            "finance",
            "2026-03-02T12:00:00Z",
            "2026-03-02T12:00:00Z",
            None,
            "tests",
            json.dumps(old_payload),
            1,
            "normal",
        ),
    )
    event_id = cursor.lastrowid
    conn.commit()

    # v1 upcaster: increment value by 10
    def up_v1(p):
        return {**p, "value": p["value"] + 10}

    # v2 upcaster: increment value by 100
    def up_v2(p):
        return {**p, "value": p["value"] + 100}

    cleanup_v1 = _register_upcaster("finance.transactions_imported", 1, up_v1)
    cleanup_v2 = _register_upcaster("finance.transactions_imported", 2, up_v2)
    try:
        events = query_events(conn, event_type="finance.transactions_imported")
        target = next(e for e in events if e.id == event_id)
        # schema_version=1: v1 runs (0→10), then v2 runs (10→110)
        assert target.payload["value"] == 110
    finally:
        cleanup_v1()
        cleanup_v2()


def test_current_schema_version_events_are_not_upcasted(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    current_payload = {"value": 42}
    cursor = conn.execute(
        """
        INSERT INTO events (
            event_type, domain, occurred_at, recorded_at,
            entity_ref, source, payload, schema_version, sensitivity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "finance.transactions_imported",
            "finance",
            "2026-03-03T12:00:00Z",
            "2026-03-03T12:00:00Z",
            None,
            "tests",
            json.dumps(current_payload),
            2,  # current version: no upcaster should apply
            "normal",
        ),
    )
    event_id = cursor.lastrowid
    conn.commit()

    # Only register upcaster for v1 — events at v2 should not be touched
    def up_v1(p):
        return {**p, "value": p["value"] + 999}

    cleanup = _register_upcaster("finance.transactions_imported", 1, up_v1)
    try:
        events = query_events(conn, event_type="finance.transactions_imported")
        target = next(e for e in events if e.id == event_id)
        # schema_version=2; v1 upcaster only applies when schema_version <= 1
        assert target.payload["value"] == 42
    finally:
        cleanup()
