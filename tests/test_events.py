import json
import sqlite3

from minx_mcp.db import get_connection
import pytest

from minx_mcp.core.events import Event, UnknownEventTypeError, emit_event, query_events


def _imported_payload(**overrides):
    payload = {
        "account_name": "Checking",
        "account_id": 1,
        "job_id": "job-123",
        "transaction_count": 2,
        "total_cents": -1234,
        "source_kind": "csv",
    }
    payload.update(overrides)
    return payload


def _categorized_payload(**overrides):
    payload = {
        "count": 2,
        "categories": ["Dining Out", "Groceries"],
    }
    payload.update(overrides)
    return payload


def _report_payload(**overrides):
    payload = {
        "report_type": "weekly",
        "period_start": "2026-01-01",
        "period_end": "2026-01-07",
        "vault_path": "Finance/Reports/2026-01-07.md",
    }
    payload.update(overrides)
    return payload


def _anomalies_payload(**overrides):
    payload = {
        "count": 3,
        "total_cents": -4500,
    }
    payload.update(overrides)
    return payload


def _insert_raw_event(
    conn,
    *,
    event_type,
    domain,
    occurred_at,
    entity_ref="entity-1",
    source="tests",
    payload=None,
    schema_version=1,
    sensitivity="normal",
):
    payload_json = json.dumps(payload or {"ok": True})
    cursor = conn.execute(
        """
        INSERT INTO events (
            event_type,
            domain,
            occurred_at,
            recorded_at,
            entity_ref,
            source,
            payload,
            schema_version,
            sensitivity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            domain,
            occurred_at,
            "2026-01-01T00:00:00Z",
            entity_ref,
            source,
            payload_json,
            schema_version,
            sensitivity,
        ),
    )
    return cursor.lastrowid


def _seed_event(conn, *, event_type, occurred_at, domain="finance", payload=None):
    payload = payload or {
        "finance.transactions_imported": _imported_payload(),
        "finance.transactions_categorized": _categorized_payload(),
        "finance.report_generated": _report_payload(),
        "finance.anomalies_detected": _anomalies_payload(),
    }[event_type]
    event_id = emit_event(
        conn,
        event_type=event_type,
        domain=domain,
        occurred_at=occurred_at,
        entity_ref="entity-1",
        source="tests",
        payload=payload,
    )
    assert event_id is not None
    return event_id


def test_emit_event_inserts_validated_json_payload_and_recorded_at(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    event_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-01-15T18:30:00Z",
        entity_ref="batch-1",
        source="finance.service",
        payload=_imported_payload(),
    )
    assert event_id is not None

    row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()

    assert row["event_type"] == "finance.transactions_imported"
    assert row["domain"] == "finance"
    assert row["occurred_at"] == "2026-01-15T18:30:00.000000Z"
    assert row["entity_ref"] == "batch-1"
    assert row["source"] == "finance.service"
    assert row["schema_version"] == 1
    assert row["sensitivity"] == "normal"
    assert row["recorded_at"].endswith("Z")
    assert json.loads(row["payload"]) == _imported_payload()


def test_emit_event_returns_none_and_logs_on_payload_validation_failure(tmp_path, caplog):
    conn = get_connection(tmp_path / "minx.db")

    event_id = emit_event(
        conn,
        event_type="finance.report_generated",
        domain="finance",
        occurred_at="2026-01-15T18:30:00Z",
        entity_ref="report-1",
        source="finance.service",
        payload=_report_payload(report_type="daily"),
    )

    assert event_id is None
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert "finance.report_generated" in caplog.text


def test_emit_event_returns_none_and_logs_on_unexpected_insert_failure(tmp_path, caplog):
    conn = get_connection(tmp_path / "minx.db")

    class BrokenConnection:
        def execute(self, sql, params=()):
            raise sqlite3.OperationalError("insert failed")

    event_id = emit_event(
        BrokenConnection(),
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-01-15T18:30:00Z",
        entity_ref="batch-1",
        source="finance.service",
        payload=_imported_payload(),
    )

    assert event_id is None
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    assert "insert failed" in caplog.text


def test_emit_event_raises_on_unknown_event_type(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    with pytest.raises(UnknownEventTypeError, match="finance.unknown_event"):
        emit_event(
            conn,
            event_type="finance.unknown_event",
            domain="finance",
            occurred_at="2026-01-15T18:30:00Z",
            entity_ref="entity-1",
            source="finance.service",
            payload={"anything": "goes"},
        )

    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_emitted_events_commit_with_caller_transaction(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    observer = get_connection(db_path)

    conn.execute("BEGIN")
    event_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-01-15T18:30:00Z",
        entity_ref="batch-1",
        source="finance.service",
        payload=_imported_payload(),
    )

    assert event_id is not None
    assert observer.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

    conn.commit()

    assert observer.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_emitted_events_rollback_with_caller_transaction(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    observer = get_connection(db_path)

    conn.execute("BEGIN")
    event_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-01-15T18:30:00Z",
        entity_ref="batch-1",
        source="finance.service",
        payload=_imported_payload(),
    )

    assert event_id is not None
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1

    conn.rollback()

    assert observer.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_query_events_filters_by_domain(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    finance_id = _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-01-15T12:00:00Z",
    )
    _insert_raw_event(
        conn,
        event_type="health.check_recorded",
        domain="health",
        occurred_at="2026-01-15T12:00:00Z",
    )
    conn.commit()

    events = query_events(conn, domain="finance")

    assert [event.id for event in events] == [finance_id]
    assert all(event.domain == "finance" for event in events)


def test_query_events_filters_by_event_type(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    imported_id = _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-01-15T12:00:00Z",
    )
    _seed_event(
        conn,
        event_type="finance.transactions_categorized",
        occurred_at="2026-01-15T13:00:00Z",
    )
    conn.commit()

    events = query_events(conn, event_type="finance.transactions_imported")

    assert [event.id for event in events] == [imported_id]
    assert all(event.event_type == "finance.transactions_imported" for event in events)


def test_query_events_filters_utc_timestamps_when_timezone_is_none(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    before_id = _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-01-15T11:59:59Z",
    )
    inside_id = _seed_event(
        conn,
        event_type="finance.transactions_categorized",
        occurred_at="2026-01-15T12:00:00Z",
    )
    second_inside_id = _seed_event(
        conn,
        event_type="finance.report_generated",
        occurred_at="2026-01-15T12:59:59Z",
    )
    after_id = _seed_event(
        conn,
        event_type="finance.anomalies_detected",
        occurred_at="2026-01-15T13:00:00Z",
    )
    conn.commit()

    events = query_events(
        conn,
        start="2026-01-15T12:00:00Z",
        end="2026-01-15T13:00:00Z",
        timezone=None,
    )

    assert [event.id for event in events] == [inside_id, second_inside_id]
    assert before_id not in [event.id for event in events]
    assert after_id not in [event.id for event in events]


def test_emit_event_normalizes_offset_timestamp_for_utc_queries(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    event_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-01-15T12:00:00-05:00",
        entity_ref="batch-1",
        source="finance.service",
        payload=_imported_payload(),
    )
    conn.commit()

    assert event_id is not None

    row = conn.execute("SELECT occurred_at FROM events WHERE id = ?", (event_id,)).fetchone()
    assert row["occurred_at"] == "2026-01-15T17:00:00.000000Z"

    events = query_events(
        conn,
        start="2026-01-15T17:00:00Z",
        end="2026-01-15T17:00:01Z",
        timezone=None,
    )

    assert [event.id for event in events] == [event_id]


def test_query_events_handles_mixed_precision_utc_timestamps_within_one_second(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    exact_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-01-15T17:00:00.000000Z",
        entity_ref="batch-exact",
        source="finance.service",
        payload=_imported_payload(job_id="job-exact"),
    )
    fractional_id = emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-01-15T17:00:00.100000Z",
        entity_ref="batch-fractional",
        source="finance.service",
        payload=_imported_payload(job_id="job-fractional"),
    )
    conn.commit()

    assert exact_id is not None
    assert fractional_id is not None

    events = query_events(
        conn,
        start="2026-01-15T17:00:00Z",
        end="2026-01-15T17:00:01Z",
        timezone=None,
    )

    assert [event.id for event in events] == [exact_id, fractional_id]
    assert [event.occurred_at for event in events] == [
        "2026-01-15T17:00:00.000000Z",
        "2026-01-15T17:00:00.100000Z",
    ]


def test_query_events_filters_local_dates_in_new_york(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-01-15T04:59:59Z",
    )
    first_id = _seed_event(
        conn,
        event_type="finance.transactions_categorized",
        occurred_at="2026-01-15T05:00:00Z",
    )
    second_id = _seed_event(
        conn,
        event_type="finance.report_generated",
        occurred_at="2026-01-16T04:59:59Z",
    )
    _seed_event(
        conn,
        event_type="finance.anomalies_detected",
        occurred_at="2026-01-16T05:00:00Z",
    )
    conn.commit()

    events = query_events(
        conn,
        start="2026-01-15",
        end="2026-01-15",
        timezone="America/New_York",
    )

    assert [event.id for event in events] == [first_id, second_id]


def test_query_events_handles_dst_spring_forward_local_day(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-03-08T04:59:59Z",
    )
    first_id = _seed_event(
        conn,
        event_type="finance.transactions_categorized",
        occurred_at="2026-03-08T05:00:00Z",
    )
    second_id = _seed_event(
        conn,
        event_type="finance.report_generated",
        occurred_at="2026-03-09T03:59:59Z",
    )
    _seed_event(
        conn,
        event_type="finance.anomalies_detected",
        occurred_at="2026-03-09T04:00:00Z",
    )
    conn.commit()

    events = query_events(
        conn,
        start="2026-03-08",
        end="2026-03-08",
        timezone="America/New_York",
    )

    assert [event.id for event in events] == [first_id, second_id]


def test_query_events_handles_dst_fall_back_local_day(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-11-01T03:59:59Z",
    )
    first_id = _seed_event(
        conn,
        event_type="finance.transactions_categorized",
        occurred_at="2026-11-01T04:00:00Z",
    )
    second_id = _seed_event(
        conn,
        event_type="finance.report_generated",
        occurred_at="2026-11-02T04:59:59Z",
    )
    _seed_event(
        conn,
        event_type="finance.anomalies_detected",
        occurred_at="2026-11-02T05:00:00Z",
    )
    conn.commit()

    events = query_events(
        conn,
        start="2026-11-01",
        end="2026-11-01",
        timezone="America/New_York",
    )

    assert [event.id for event in events] == [first_id, second_id]


def test_query_events_uses_inclusive_local_start_and_exclusive_next_midnight_end(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    start_id = _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-01-15T05:00:00Z",
    )
    inside_id = _seed_event(
        conn,
        event_type="finance.transactions_categorized",
        occurred_at="2026-01-16T04:59:59Z",
    )
    _seed_event(
        conn,
        event_type="finance.report_generated",
        occurred_at="2026-01-16T05:00:00Z",
    )
    conn.commit()

    events = query_events(
        conn,
        start="2026-01-15",
        end="2026-01-15",
        timezone="America/New_York",
    )

    assert [event.id for event in events] == [start_id, inside_id]


def test_query_events_composes_domain_type_and_date_filters_together(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_event(
        conn,
        event_type="finance.transactions_imported",
        occurred_at="2026-01-15T15:00:00Z",
    )
    target_id = _seed_event(
        conn,
        event_type="finance.report_generated",
        occurred_at="2026-01-15T17:00:00Z",
    )
    _seed_event(
        conn,
        event_type="finance.report_generated",
        occurred_at="2026-01-16T06:00:00Z",
    )
    _insert_raw_event(
        conn,
        event_type="health.report_generated",
        domain="health",
        occurred_at="2026-01-15T17:00:00Z",
    )
    conn.commit()

    events = query_events(
        conn,
        domain="finance",
        event_type="finance.report_generated",
        start="2026-01-15",
        end="2026-01-15",
        timezone="America/New_York",
    )

    assert len(events) == 1
    assert events[0] == Event(
        id=target_id,
        event_type="finance.report_generated",
        domain="finance",
        occurred_at="2026-01-15T17:00:00.000000Z",
        recorded_at=events[0].recorded_at,
        entity_ref="entity-1",
        source="tests",
        payload=_report_payload(),
        schema_version=1,
        sensitivity="normal",
    )
