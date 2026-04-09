from __future__ import annotations

from minx_mcp.core.events import emit_event
from minx_mcp.db import get_connection
from minx_mcp.preferences import set_preference


def test_build_daily_timeline_returns_deterministic_summaries_for_supported_event_types(
    tmp_path,
):
    conn = get_connection(tmp_path / "minx.db")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T05:15:00Z",
        payload={
            "account_name": "Checking",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 3,
            "total_cents": -4525,
            "source_kind": "csv",
        },
    )
    _seed_event(
        conn,
        event_type="finance.transactions_categorized",
        occurred_at="2026-03-15T12:30:00Z",
        payload={
            "count": 2,
            "categories": ["Dining Out", "Groceries"],
        },
    )
    _seed_event(
        conn,
        event_type="finance.report_generated",
        occurred_at="2026-03-15T18:00:00Z",
        payload={
            "report_type": "weekly",
            "period_start": "2026-03-08",
            "period_end": "2026-03-14",
            "vault_path": "Finance/Reports/2026-03-14.md",
        },
    )
    _seed_event(
        conn,
        event_type="finance.anomalies_detected",
        occurred_at="2026-03-15T19:45:00Z",
        payload={
            "count": 3,
            "total_cents": -2700,
        },
    )
    conn.commit()

    from minx_mcp.core.read_models import build_daily_timeline

    timeline = build_daily_timeline(conn, "2026-03-15")

    assert timeline.date == "2026-03-15"
    assert [(entry.event_type, entry.summary) for entry in timeline.entries] == [
        (
            "finance.transactions_imported",
            "Imported 3 transactions from Checking via csv (net -$45.25)",
        ),
        (
            "finance.transactions_categorized",
            "Categorized 2 transactions into Dining Out, Groceries",
        ),
        (
            "finance.report_generated",
            "Generated weekly report for 2026-03-08 to 2026-03-14",
        ),
        (
            "finance.anomalies_detected",
            "Detected 3 anomalies totaling -$27.00",
        ),
    ]


def test_build_daily_timeline_uses_stored_timezone_preference(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    set_preference(conn, "core", "timezone", "America/Los_Angeles")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T06:59:59Z",
        payload=_imported_payload(job_id="job-before"),
    )
    included_id = _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T07:00:00Z",
        payload=_imported_payload(job_id="job-inside"),
    )
    conn.commit()

    from minx_mcp.core.read_models import build_daily_timeline

    timeline = build_daily_timeline(conn, "2026-03-15")

    assert [entry.entity_ref for entry in timeline.entries] == [f"entity-{included_id}"]


def test_build_daily_timeline_falls_back_to_machine_local_timezone_when_preference_missing(
    tmp_path,
    monkeypatch,
):
    conn = get_connection(tmp_path / "minx.db")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T04:59:59Z",
        payload=_imported_payload(job_id="job-before"),
    )
    included_id = _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T05:00:00Z",
        payload=_imported_payload(job_id="job-inside"),
    )
    conn.commit()

    import minx_mcp.core.read_models as read_models

    monkeypatch.setattr(
        read_models,
        "_get_machine_local_timezone_name",
        lambda: "America/Chicago",
    )

    timeline = read_models.build_daily_timeline(conn, "2026-03-15")

    assert [entry.entity_ref for entry in timeline.entries] == [f"entity-{included_id}"]


def test_build_daily_timeline_returns_empty_entries_for_quiet_day(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    from minx_mcp.core.read_models import build_daily_timeline

    timeline = build_daily_timeline(conn, "2026-03-15")

    assert timeline.date == "2026-03-15"
    assert timeline.entries == []


def test_build_daily_timeline_excludes_sensitive_events_from_review_output(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T05:15:00Z",
        payload=_imported_payload(job_id="job-normal"),
        sensitivity="normal",
    )
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T06:15:00Z",
        payload=_imported_payload(job_id="job-sensitive"),
        sensitivity="sensitive",
    )
    conn.commit()

    from minx_mcp.core.read_models import build_daily_timeline

    timeline = build_daily_timeline(conn, "2026-03-15")

    assert len(timeline.entries) == 1
    assert timeline.entries[0].entity_ref == "entity-1"


def test_build_spending_snapshot_uses_finance_read_api_and_two_week_comparison(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Groceries",
        merchant="HEB",
        amount_cents=-4500,
        category_id=2,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Coffee",
        merchant="Cafe",
        amount_cents=-1200,
        category_id=3,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Mystery",
        merchant="Unknown",
        amount_cents=-300,
        category_id=1,
    )
    for offset in range(7):
        _insert_transaction(
            conn,
            posted_at=f"2026-03-{2 + offset:02d}",
                description=f"Prior Groceries {offset}",
                merchant="HEB",
                amount_cents=-1000,
                category_id=2,
            )
    conn.commit()

    from minx_mcp.core.read_models import build_spending_snapshot

    snapshot = build_spending_snapshot(conn, "2026-03-15")

    assert snapshot.date == "2026-03-15"
    assert snapshot.total_spent_cents == 6000
    assert snapshot.by_category == {
        "Groceries": 4500,
        "Dining Out": 1200,
        "Uncategorized": 300,
    }
    assert snapshot.top_merchants == [("HEB", 4500), ("Cafe", 1200), ("Unknown", 300)]
    assert snapshot.vs_prior_week_pct == -14.29
    assert snapshot.uncategorized_count == 1
    assert snapshot.uncategorized_total_cents == 300


def test_build_spending_snapshot_returns_none_for_vs_prior_week_when_less_than_two_weeks_exist(
    tmp_path,
):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Only Current Week",
        merchant="Store",
        amount_cents=-2500,
        category_id=2,
    )
    conn.commit()

    from minx_mcp.core.read_models import build_spending_snapshot

    snapshot = build_spending_snapshot(conn, "2026-03-15")

    assert snapshot.total_spent_cents == 2500
    assert snapshot.vs_prior_week_pct is None


def test_build_spending_snapshot_returns_zeroes_for_quiet_day(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    from minx_mcp.core.read_models import build_spending_snapshot

    snapshot = build_spending_snapshot(conn, "2026-03-15")

    assert snapshot.date == "2026-03-15"
    assert snapshot.total_spent_cents == 0
    assert snapshot.by_category == {}
    assert snapshot.top_merchants == []
    assert snapshot.vs_prior_week_pct is None
    assert snapshot.uncategorized_count == 0
    assert snapshot.uncategorized_total_cents == 0


def test_build_open_loops_snapshot_includes_uncategorized_and_import_job_issues(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Mystery",
        merchant="Unknown",
        amount_cents=-3400,
        category_id=1,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Another Mystery",
        merchant="Unknown",
        amount_cents=-125,
        category_id=1,
    )
    conn.execute(
        """
        INSERT INTO jobs (id, job_type, status, requested_by, source_ref, error_message, updated_at)
        VALUES
            ('job-failed', 'finance_import', 'failed', 'test', '/imports/a.csv', 'bad csv', '2026-03-15 09:00:00'),
            ('job-stale', 'finance_import', 'running', 'test', '/imports/b.csv', NULL, datetime('now', '-31 minutes'))
        """
    )
    conn.commit()

    from minx_mcp.core.read_models import build_open_loops_snapshot

    snapshot = build_open_loops_snapshot(conn, "2026-03-15")

    assert snapshot.date == "2026-03-15"
    assert [(loop.loop_type, loop.description, loop.count, loop.severity) for loop in snapshot.loops] == [
        (
            "uncategorized_transactions",
            "2 uncategorized transactions totaling $35.25",
            2,
            "info",
        ),
        (
            "failed_import_job",
            "Import job job-failed failed for /imports/a.csv",
            1,
            "warning",
        ),
        (
            "stale_import_job",
            "Import job job-stale is stale for /imports/b.csv",
            1,
            "warning",
        ),
    ]


def test_build_open_loops_snapshot_returns_empty_for_quiet_day(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    from minx_mcp.core.read_models import build_open_loops_snapshot

    snapshot = build_open_loops_snapshot(conn, "2026-03-15")

    assert snapshot.date == "2026-03-15"
    assert snapshot.loops == []


def test_build_open_loops_snapshot_ignores_uncategorized_inflow_only_day(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Refund",
        merchant="Unknown",
        amount_cents=3400,
        category_id=1,
    )
    conn.commit()

    from minx_mcp.core.read_models import build_open_loops_snapshot

    snapshot = build_open_loops_snapshot(conn, "2026-03-15")

    assert snapshot.loops == []


def test_build_read_models_uses_stored_timezone_preference(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    set_preference(conn, "core", "timezone", "America/Los_Angeles")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T06:59:59Z",
        payload=_imported_payload(job_id="job-before"),
    )
    included_id = _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T07:00:00Z",
        payload=_imported_payload(job_id="job-inside"),
    )
    conn.commit()

    from minx_mcp.core.read_models import build_read_models

    read_models = build_read_models(conn, "2026-03-15")

    assert [entry.entity_ref for entry in read_models.timeline.entries] == [
        f"entity-{included_id}"
    ]


def test_build_read_models_falls_back_to_machine_local_timezone_when_preference_missing(
    tmp_path,
    monkeypatch,
):
    conn = get_connection(tmp_path / "minx.db")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T04:59:59Z",
        payload=_imported_payload(job_id="job-before"),
    )
    included_id = _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-15T05:00:00Z",
        payload=_imported_payload(job_id="job-inside"),
    )
    conn.commit()

    import minx_mcp.core.read_models as read_models

    monkeypatch.setattr(
        read_models,
        "_get_machine_local_timezone_name",
        lambda: "America/Chicago",
    )

    built = read_models.build_read_models(conn, "2026-03-15")

    assert [entry.entity_ref for entry in built.timeline.entries] == [f"entity-{included_id}"]


def test_build_read_models_returns_bundle(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    from minx_mcp.core.read_models import build_read_models

    read_models = build_read_models(conn, "2026-03-15")

    assert read_models.timeline.date == "2026-03-15"
    assert read_models.spending.date == "2026-03-15"
    assert read_models.open_loops.date == "2026-03-15"


def _seed_event(
    conn,
    *,
    event_type: str,
    occurred_at: str,
    payload: dict,
    sensitivity: str = "normal",
) -> int:
    event_id = emit_event(
        conn,
        event_type=event_type,
        domain="finance",
        occurred_at=occurred_at,
        entity_ref="entity-pending",
        source="tests",
        payload=payload,
        sensitivity=sensitivity,
    )
    assert event_id is not None
    conn.execute(
        "UPDATE events SET entity_ref = ? WHERE id = ?",
        (f"entity-{event_id}", event_id),
    )
    return event_id


def _imported_payload(**overrides):
    payload = {
        "account_name": "Checking",
        "account_id": 1,
        "job_id": "job-123",
        "transaction_count": 1,
        "total_cents": -1000,
        "source_kind": "csv",
    }
    payload.update(overrides)
    return payload


def _seed_batch(conn) -> None:
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fingerprint')
        """
    )


def _insert_transaction(
    conn,
    *,
    posted_at: str,
    description: str,
    merchant: str,
    amount_cents: int,
    category_id: int,
) -> None:
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
        (1, 1, posted_at, description, merchant, amount_cents, category_id, "manual"),
    )
