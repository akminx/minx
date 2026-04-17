"""Tests for `minx_mcp.finance.import_workflow.run_finance_import` orchestration."""

from __future__ import annotations

from pathlib import Path

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.db import get_connection
from minx_mcp.finance import import_workflow as import_workflow_module
from minx_mcp.finance.import_models import MAX_FINANCE_IMPORT_ROWS, ParsedImportBatch, ParsedTransaction
from minx_mcp.finance.import_workflow import run_finance_import
from minx_mcp.finance.service import FinanceService
from minx_mcp.jobs import get_job


def _minimal_dcu_batch(*, content_hash: str, source_path: Path, account_name: str = "DCU") -> ParsedImportBatch:
    return ParsedImportBatch(
        account_name=account_name,
        source_type="csv",
        source_ref=str(source_path.resolve()),
        raw_fingerprint=content_hash,
        transactions=[
            ParsedTransaction(
                posted_at="2026-03-02",
                description="H-E-B",
                amount_cents=-4520,
                merchant="H-E-B",
                category_hint=None,
                external_id=None,
            )
        ],
    )


def test_import_workflow_does_not_hold_db_lock_during_parse(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "minx.db"
    service = FinanceService(db_path, tmp_path)
    source = tmp_path / "stmt.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n",
        encoding="utf-8",
    )

    lock_probe_succeeded: list[bool] = []

    def fake_parse(
        _canonical_path: Path,
        account_name: str,
        _kind: str | None,
        _mapping: dict[str, object] | None = None,
        *,
        snapshot_path: Path | None = None,
        content_hash: str | None = None,
    ) -> ParsedImportBatch:
        second = get_connection(Path(db_path))
        try:
            second.execute("BEGIN IMMEDIATE")
            second.execute("ROLLBACK")
            lock_probe_succeeded.append(True)
        except Exception:
            lock_probe_succeeded.append(False)
            raise
        finally:
            second.close()
        assert snapshot_path is not None and content_hash is not None
        return _minimal_dcu_batch(content_hash=content_hash, source_path=source, account_name=account_name)

    monkeypatch.setattr(import_workflow_module, "parse_source_file", fake_parse)

    result = run_finance_import(service, str(source), account_name="DCU")

    assert lock_probe_succeeded == [True]
    assert result["status"] == "completed"
    assert result["result"] is not None
    ins = result["result"]
    assert ins["inserted"] == 1  # type: ignore[index]
    assert ins["skipped"] == 0  # type: ignore[index]


def test_import_workflow_commit_succeeds_after_parse(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "minx.db"
    service = FinanceService(db_path, tmp_path)
    source = tmp_path / "stmt.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n",
        encoding="utf-8",
    )

    def fake_parse(
        _canonical_path: Path,
        account_name: str,
        _kind: str | None,
        _mapping: dict[str, object] | None = None,
        *,
        snapshot_path: Path | None = None,
        content_hash: str | None = None,
    ) -> ParsedImportBatch:
        assert snapshot_path is not None and content_hash is not None
        return _minimal_dcu_batch(content_hash=content_hash, source_path=source, account_name=account_name)

    monkeypatch.setattr(import_workflow_module, "parse_source_file", fake_parse)

    result = run_finance_import(service, str(source), account_name="DCU")
    res = result["result"]
    assert isinstance(res, dict)
    batch_id = int(res["batch_id"])

    row = service.conn.execute(
        "SELECT COUNT(*) AS c FROM finance_transactions WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    assert int(row["c"]) == 1


def test_import_workflow_respects_bounded_limits(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "minx.db"
    service = FinanceService(db_path, tmp_path)
    source = tmp_path / "stmt.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n",
        encoding="utf-8",
    )

    def raise_oversized(_resolved: Path, _dest: Path) -> str:
        raise InvalidInputError(
            "Finance import source exceeds maximum allowed size (52428800 bytes)",
        )

    real_stream = import_workflow_module.stream_snapshot_copy_and_hash
    monkeypatch.setattr(import_workflow_module, "stream_snapshot_copy_and_hash", raise_oversized)
    with pytest.raises(InvalidInputError, match="Finance import source exceeds"):
        run_finance_import(service, str(source), account_name="DCU")

    def raise_too_many_rows(
        *_args: object,
        **_kwargs: object,
    ) -> ParsedImportBatch:
        raise InvalidInputError(
            f"Finance import exceeds maximum row count ({MAX_FINANCE_IMPORT_ROWS} rows)",
        )

    monkeypatch.setattr(import_workflow_module, "stream_snapshot_copy_and_hash", real_stream)
    monkeypatch.setattr(import_workflow_module, "parse_source_file", raise_too_many_rows)
    with pytest.raises(InvalidInputError, match="maximum row count"):
        run_finance_import(service, str(source), account_name="DCU")

    job = service.conn.execute(
        "SELECT id FROM jobs WHERE job_type = 'finance_import' ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    assert job is not None
    job_row = get_job(service.conn, str(job["id"]))
    assert job_row is not None
    assert job_row["status"] == "failed"
