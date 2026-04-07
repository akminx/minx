import logging

import pytest

from minx_mcp.core import events as core_events
from minx_mcp.core.events import query_events
from minx_mcp.db import get_connection
from minx_mcp.finance.service import FinanceService


def _import_source(tmp_path, amount: str = "-45.20", description: str = "H-E-B"):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        f"2026-03-02,{description},Withdrawal,{amount}\n"
    )
    return source


def _seed_transaction(service: FinanceService, tmp_path):
    source = _import_source(tmp_path)
    service.finance_import(str(source), account_name="DCU", source_kind="dcu_csv")
    return service.sensitive_finance_query(limit=1)["transactions"][0]


def _invalid_emit_event(db, event_type, domain, occurred_at, entity_ref, source, payload, **kwargs):
    invalid_payload = dict(payload)
    if event_type == "finance.transactions_imported":
        invalid_payload["transaction_count"] = "bad"
    elif event_type == "finance.transactions_categorized":
        invalid_payload["count"] = "bad"
    elif event_type == "finance.report_generated":
        invalid_payload["report_type"] = "daily"
    elif event_type == "finance.anomalies_detected":
        invalid_payload["count"] = "bad"
    return core_events.emit_event(
        db,
        event_type=event_type,
        domain=domain,
        occurred_at=occurred_at,
        entity_ref=entity_ref,
        source=source,
        payload=invalid_payload,
        **kwargs,
    )


def test_finance_import_emits_event_on_success(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    source = _import_source(tmp_path)

    result = service.finance_import(str(source), account_name="DCU", source_kind="dcu_csv")
    account_id = service.conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]

    events = query_events(service.conn, event_type="finance.transactions_imported")

    assert len(events) == 1
    assert events[0].domain == "finance"
    assert events[0].source == "finance.service"
    assert events[0].entity_ref == str(result["result"]["batch_id"])
    assert events[0].sensitivity == "normal"
    assert events[0].payload == {
        "account_name": "DCU",
        "account_id": account_id,
        "job_id": result["job_id"],
        "transaction_count": 1,
        "total_cents": -4520,
        "source_kind": "dcu_csv",
    }


def test_finance_categorize_emits_event_on_success(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    transaction = _seed_transaction(service, tmp_path)

    changed = service.finance_categorize([transaction["id"]], "Dining Out")

    events = query_events(service.conn, event_type="finance.transactions_categorized")

    assert changed == 1
    assert len(events) == 1
    assert events[0].domain == "finance"
    assert events[0].source == "finance.service"
    assert events[0].payload == {
        "count": 1,
        "categories": ["Dining Out"],
    }


@pytest.mark.parametrize(
    ("method_name", "period_start", "period_end", "report_type"),
    [
        ("generate_weekly_report", "2026-03-02", "2026-03-08", "weekly"),
        ("generate_monthly_report", "2026-03-01", "2026-03-31", "monthly"),
    ],
)
def test_report_generation_emits_event_on_success(
    tmp_path, method_name, period_start, period_end, report_type
):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    _seed_transaction(service, tmp_path)

    result = getattr(service, method_name)(period_start, period_end)

    events = query_events(service.conn, event_type="finance.report_generated")

    assert len(events) == 1
    assert events[0].domain == "finance"
    assert events[0].source == "finance.service"
    assert events[0].payload == {
        "report_type": report_type,
        "period_start": period_start,
        "period_end": period_end,
        "vault_path": result["vault_path"],
    }


def test_finance_anomalies_emits_event_when_results_found(tmp_path):
    db_path = tmp_path / "minx.db"
    service = FinanceService(db_path, tmp_path)
    observer = get_connection(db_path)
    source = _import_source(tmp_path, amount="-500.00", description="Unknown Merchant")
    service.finance_import(str(source), account_name="DCU", source_kind="dcu_csv")

    result = service.finance_anomalies()
    events = query_events(observer, event_type="finance.anomalies_detected")

    assert len(result["items"]) == 1
    assert len(events) == 1
    assert events[0].domain == "finance"
    assert events[0].source == "finance.service"
    assert events[0].payload == {
        "count": 1,
        "total_cents": -50000,
    }


def test_finance_anomalies_does_not_commit_preexisting_transaction(tmp_path):
    db_path = tmp_path / "minx.db"
    service = FinanceService(db_path, tmp_path)
    observer = get_connection(db_path)
    source = _import_source(tmp_path, amount="-500.00", description="Unknown Merchant")
    service.finance_import(str(source), account_name="DCU", source_kind="dcu_csv")

    service.conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json)
        VALUES ('core', 'timezone', '\"America/Chicago\"')
        """
    )

    result = service.finance_anomalies()

    assert len(result["items"]) == 1
    assert len(query_events(service.conn, event_type="finance.anomalies_detected")) == 1
    assert query_events(observer, event_type="finance.anomalies_detected") == []
    assert observer.execute(
        "SELECT value_json FROM preferences WHERE domain = 'core' AND key = 'timezone'"
    ).fetchone() is None


def test_finance_anomalies_event_total_uses_stored_cents(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    service = FinanceService(db_path, tmp_path)
    observer = get_connection(db_path)
    source = _import_source(tmp_path, amount="-500.00", description="Unknown Merchant")
    service.finance_import(str(source), account_name="DCU", source_kind="dcu_csv")
    transaction_id = service.sensitive_finance_query(limit=1)["transactions"][0]["id"]

    def fake_find_anomalies(conn):
        return [
            {
                "kind": "large_uncategorized",
                "transaction_id": transaction_id,
                "posted_at": "2026-03-02",
                "description": "Unknown Merchant",
                "amount": -0.01,
            }
        ]

    monkeypatch.setattr("minx_mcp.finance.service.find_anomalies", fake_find_anomalies)

    service.finance_anomalies()
    events = query_events(observer, event_type="finance.anomalies_detected")

    assert len(events) == 1
    assert events[0].payload["total_cents"] == -50000


@pytest.mark.parametrize("operation_name", ["import", "categorize", "weekly_report", "anomalies"])
def test_finance_operations_log_event_emission_failures_without_breaking_success(
    tmp_path, monkeypatch, caplog, operation_name
):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    monkeypatch.setattr("minx_mcp.finance.service.emit_event", _invalid_emit_event)
    caplog.set_level(logging.WARNING)

    if operation_name == "import":
        source = _import_source(tmp_path)
        result = service.finance_import(str(source), account_name="DCU", source_kind="dcu_csv")
        assert result["status"] == "completed"
        assert service.conn.execute(
            "SELECT COUNT(*) AS count FROM finance_transactions"
        ).fetchone()["count"] == 1
        event_type = "finance.transactions_imported"
    elif operation_name == "categorize":
        transaction = _seed_transaction(service, tmp_path)
        changed = service.finance_categorize([transaction["id"]], "Dining Out")
        assert changed == 1
        assert service.sensitive_finance_query(limit=1)["transactions"][0]["category_name"] == "Dining Out"
        event_type = "finance.transactions_categorized"
    elif operation_name == "weekly_report":
        _seed_transaction(service, tmp_path)
        report = service.generate_weekly_report("2026-03-02", "2026-03-08")
        assert report["vault_path"].endswith("Finance/weekly-2026-03-02.md")
        assert service.conn.execute(
            "SELECT COUNT(*) AS count FROM finance_report_runs"
        ).fetchone()["count"] == 1
        event_type = "finance.report_generated"
    else:
        source = _import_source(tmp_path, amount="-500.00", description="Unknown Merchant")
        service.finance_import(str(source), account_name="DCU", source_kind="dcu_csv")
        anomalies = service.finance_anomalies()
        assert len(anomalies["items"]) == 1
        event_type = "finance.anomalies_detected"

    assert query_events(service.conn, event_type=event_type) == []
    assert event_type in caplog.text


def test_finance_import_rollback_does_not_leave_committed_event(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    service = FinanceService(db_path, tmp_path)
    observer = get_connection(db_path)
    source = _import_source(tmp_path)

    def fail_before_commit(conn, job_id, result, *, commit=True):
        assert len(query_events(conn, event_type="finance.transactions_imported")) == 1
        raise RuntimeError("commit blocked")

    monkeypatch.setattr("minx_mcp.finance.service.mark_completed", fail_before_commit)

    with pytest.raises(RuntimeError, match="commit blocked"):
        service.finance_import(str(source), account_name="DCU", source_kind="dcu_csv")

    assert query_events(observer, event_type="finance.transactions_imported") == []


def test_report_generation_rollback_does_not_leave_committed_event(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    service = FinanceService(db_path, tmp_path)
    observer = get_connection(db_path)
    _seed_transaction(service, tmp_path)

    def fail_before_commit(conn, report_kind, period_start, period_end, vault_path, summary):
        assert len(query_events(conn, event_type="finance.report_generated")) == 1
        raise RuntimeError("persist blocked")

    monkeypatch.setattr("minx_mcp.finance.service.persist_report_run", fail_before_commit)

    with pytest.raises(RuntimeError, match="persist blocked"):
        service.generate_weekly_report("2026-03-02", "2026-03-08")

    assert query_events(observer, event_type="finance.report_generated") == []
