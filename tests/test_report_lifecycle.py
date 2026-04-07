from pathlib import Path

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance.reports import upsert_report_run
from minx_mcp.finance.service import FinanceService


def _seed_weekly_source(tmp_path) -> Path:
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
        "2026-03-03,Payroll,Deposit,1200.00\n"
    )
    return source


def test_generate_weekly_report_marks_run_completed(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    source = _seed_weekly_source(tmp_path)
    service.finance_import(str(source), account_name="DCU")

    result = service.generate_weekly_report("2026-03-02", "2026-03-08")
    row = service.conn.execute(
        """
        SELECT status, error_message, vault_path
        FROM finance_report_runs
        WHERE report_kind = 'weekly' AND period_start = '2026-03-02' AND period_end = '2026-03-08'
        """
    ).fetchone()

    assert row["status"] == "completed"
    assert row["error_message"] is None
    assert row["vault_path"] == result["vault_path"]


def test_generate_weekly_report_marks_run_failed_and_cleans_up_file_on_post_write_failure(
    tmp_path,
    monkeypatch,
):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    source = _seed_weekly_source(tmp_path)
    service.finance_import(str(source), account_name="DCU")

    def fail_emit(*args, **kwargs):
        raise RuntimeError("event blocked")

    monkeypatch.setattr(service, "_emit_finance_event", fail_emit)

    with pytest.raises(RuntimeError, match="event blocked"):
        service.generate_weekly_report("2026-03-02", "2026-03-08")

    row = service.conn.execute(
        """
        SELECT status, error_message, vault_path
        FROM finance_report_runs
        WHERE report_kind = 'weekly' AND period_start = '2026-03-02' AND period_end = '2026-03-08'
        """
    ).fetchone()

    assert row["status"] == "failed"
    assert "event blocked" in row["error_message"]
    assert not Path(row["vault_path"]).exists()


def test_upsert_report_run_rejects_invalid_status_in_lifecycle_context(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    service.finance_import(str(_seed_weekly_source(tmp_path)), account_name="DCU")
    with pytest.raises(InvalidInputError, match="report run status"):
        upsert_report_run(
            service.conn,
            "weekly",
            "2026-04-01",
            "2026-04-07",
            str(service.vault_writer.resolve_path("Finance/weekly-2026-04-01.md")),
            {"totals": {}},
            status="running",
            commit=False,
        )


def test_generate_monthly_report_marks_run_failed_and_cleans_up_file_on_post_write_failure(
    tmp_path,
    monkeypatch,
):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    source = _seed_weekly_source(tmp_path)
    service.finance_import(str(source), account_name="DCU")

    def fail_emit(*args, **kwargs):
        raise RuntimeError("event blocked")

    monkeypatch.setattr(service, "_emit_finance_event", fail_emit)

    with pytest.raises(RuntimeError, match="event blocked"):
        service.generate_monthly_report("2026-03-01", "2026-03-31")

    row = service.conn.execute(
        """
        SELECT status, error_message, vault_path
        FROM finance_report_runs
        WHERE report_kind = 'monthly' AND period_start = '2026-03-01' AND period_end = '2026-03-31'
        """
    ).fetchone()

    assert row["status"] == "failed"
    assert "event blocked" in row["error_message"]
    assert not Path(row["vault_path"]).exists()


def test_generate_weekly_report_repairs_failed_row_on_rerun(tmp_path, monkeypatch):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    source = _seed_weekly_source(tmp_path)
    service.finance_import(str(source), account_name="DCU")

    def fail_emit(*args, **kwargs):
        raise RuntimeError("event blocked")

    monkeypatch.setattr(service, "_emit_finance_event", fail_emit)
    with pytest.raises(RuntimeError, match="event blocked"):
        service.generate_weekly_report("2026-03-02", "2026-03-08")

    monkeypatch.setattr(service, "_emit_finance_event", lambda *args, **kwargs: 1)
    result = service.generate_weekly_report("2026-03-02", "2026-03-08")

    count = service.conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM finance_report_runs
        WHERE report_kind = 'weekly' AND period_start = '2026-03-02' AND period_end = '2026-03-08'
        """
    ).fetchone()["count"]
    row = service.conn.execute(
        """
        SELECT status, error_message, vault_path
        FROM finance_report_runs
        WHERE report_kind = 'weekly' AND period_start = '2026-03-02' AND period_end = '2026-03-08'
        """
    ).fetchone()

    assert count == 1
    assert row["status"] == "completed"
    assert row["error_message"] is None
    assert row["vault_path"] == result["vault_path"]
