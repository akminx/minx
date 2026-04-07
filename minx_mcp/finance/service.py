from __future__ import annotations

import hashlib
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from sqlite3 import Connection

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.events import emit_event
from minx_mcp.db import get_connection
from minx_mcp.finance.analytics import find_anomalies, sensitive_query, summarize_finances
from minx_mcp.finance.dedupe import fingerprint_transaction
from minx_mcp.finance.import_models import ParsedImportBatch, ParsedTransaction
from minx_mcp.finance.importers import detect_source_kind, parse_source_file
from minx_mcp.finance.reports import (
    build_monthly_report,
    build_weekly_report,
    persist_report_run,
    render_monthly_markdown,
    render_weekly_markdown,
    upsert_report_run,
)
from minx_mcp.jobs import get_job, mark_completed, mark_failed, mark_running, submit_job
from minx_mcp.preferences import get_csv_mapping
from minx_mcp.vault_writer import VaultWriter

EVENT_SOURCE = "finance.service"
logger = logging.getLogger(__name__)


class FinanceService:
    def __init__(self, db_path: Path, vault_root: Path, import_root: Path | None = None) -> None:
        self._db_path = db_path
        self._local = threading.local()
        self._uncategorized_category_id: int | None = None
        self.import_root = (import_root or db_path.parent).resolve()
        self.vault_writer = VaultWriter(vault_root, ("Finance",))

    @property
    def conn(self) -> Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = get_connection(self._db_path)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def finance_import(
        self,
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]:
        path = Path(source_ref)
        if not path.is_file():
            raise InvalidInputError("source_ref must point to an existing file")
        resolved_path = path.resolve()
        canonical_source_path = _canonicalize_existing_path(resolved_path)
        try:
            canonical_source_path.relative_to(self.import_root)
        except ValueError as exc:
            raise InvalidInputError("source_ref must be inside the allowed import root") from exc
        account = self._account(account_name)
        account_id = int(account["id"])
        effective_source_kind = source_kind or detect_source_kind(canonical_source_path)
        effective_mapping = mapping
        canonical_source_ref = str(canonical_source_path)
        if effective_source_kind == "generic_csv" and effective_mapping is None:
            profile_name = str(account["import_profile"]) if account["import_profile"] else account_name
            effective_mapping = get_csv_mapping(self.conn, profile_name)
            if effective_mapping is None and profile_name != account_name:
                effective_mapping = get_csv_mapping(self.conn, account_name)
            if effective_mapping is None:
                raise InvalidInputError(f"No saved generic CSV mapping for account: {account_name}")

        file_bytes = resolved_path.read_bytes()
        content_hash = hashlib.sha256(file_bytes).hexdigest()
        idempotency_key = hashlib.sha256(
            f"{account_name}|{canonical_source_ref}|{content_hash}".encode()
        ).hexdigest()
        job = submit_job(self.conn, "finance_import", "system", canonical_source_ref, idempotency_key)
        if job["status"] in {"completed", "running"}:
            return {"job_id": job["id"], "status": job["status"], "result": job["result"]}

        savepoint_active = False
        try:
            mark_running(self.conn, str(job["id"]), commit=False)
            self.conn.execute("SAVEPOINT finance_import")
            savepoint_active = True

            parsed = parse_source_file(
                canonical_source_path, account_name, effective_source_kind, effective_mapping,
                file_bytes=file_bytes, content_hash=content_hash,
            )
            batch_id = self._insert_batch(account_id, parsed)
            inserted = 0
            skipped = 0
            total_cents = 0

            for txn in parsed.transactions:
                fingerprint = fingerprint_transaction(account_id, txn)
                existing = self.conn.execute(
                    "SELECT transaction_id FROM finance_transaction_dedupe WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if existing:
                    skipped += 1
                    continue

                tx_id = self._insert_transaction(account_id, batch_id, txn)
                self.conn.execute(
                    "INSERT INTO finance_transaction_dedupe (fingerprint, transaction_id) VALUES (?, ?)",
                    (fingerprint, tx_id),
                )
                inserted += 1
                total_cents += txn.amount_cents

            self.conn.execute(
                "UPDATE finance_import_batches SET inserted_count = ?, skipped_count = ? WHERE id = ?",
                (inserted, skipped, batch_id),
            )
            self.conn.execute(
                "UPDATE finance_accounts SET last_imported_at = datetime('now') WHERE id = ?",
                (account_id,),
            )
            self.apply_category_rules(batch_id=batch_id, commit=False)
            self._emit_finance_event(
                event_type="finance.transactions_imported",
                entity_ref=str(batch_id),
                payload={
                    "account_name": account_name,
                    "account_id": account_id,
                    "job_id": str(job["id"]),
                    "transaction_count": inserted,
                    "total_cents": total_cents,
                    "source_kind": effective_source_kind,
                },
            )
            self.conn.execute("RELEASE SAVEPOINT finance_import")
            savepoint_active = False

            result = {"batch_id": batch_id, "inserted": inserted, "skipped": skipped}
            mark_completed(self.conn, str(job["id"]), result)
            return {"job_id": job["id"], "status": "completed", "result": result}
        except Exception as exc:
            if self.conn.in_transaction:
                if savepoint_active:
                    self.conn.execute("ROLLBACK TO SAVEPOINT finance_import")
                    self.conn.execute("RELEASE SAVEPOINT finance_import")
                else:
                    self.conn.rollback()
            mark_failed(self.conn, str(job["id"]), str(exc))
            raise

    def add_category_rule(self, category_name: str, match_kind: str, pattern: str) -> None:
        if not pattern.strip():
            raise InvalidInputError("pattern must not be empty")
        category_id = self._category_id(category_name)
        self.conn.execute(
            """
            INSERT INTO finance_category_rules (category_id, match_kind, pattern)
            VALUES (?, ?, ?)
            """,
            (category_id, match_kind, pattern),
        )
        self.conn.commit()

    def apply_category_rules(self, batch_id: int | None = None, *, commit: bool = True) -> None:
        rules = self.conn.execute(
            """
            SELECT r.pattern, r.match_kind, r.category_id
            FROM finance_category_rules r
            ORDER BY r.priority ASC, r.id ASC
            """
        ).fetchall()
        batch_clause = ""
        batch_params: tuple[object, ...] = ()
        if batch_id is not None:
            batch_clause = " AND batch_id = ?"
            batch_params = (batch_id,)
        for rule in rules:
            if rule["match_kind"] != "merchant_contains":
                continue
            escaped = (
                rule["pattern"]
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            self.conn.execute(
                """
                UPDATE finance_transactions
                SET category_id = ?, category_source = 'rule'
                WHERE merchant LIKE ? ESCAPE '\\' AND category_source != 'manual'
                """
                + batch_clause,
                (rule["category_id"], f"%{escaped}%", *batch_params),
            )
        if commit:
            self.conn.commit()

    def finance_categorize(self, transaction_ids: list[int], category_name: str) -> int:
        if not transaction_ids:
            raise InvalidInputError("transaction_ids must be a non-empty list")
        unique_ids = list(dict.fromkeys(transaction_ids))
        category_id = self._category_id(category_name)
        placeholders = ",".join("?" for _ in unique_ids)
        cursor = self.conn.execute(
            f"""
            UPDATE finance_transactions
            SET category_id = ?, category_source = 'manual'
            WHERE id IN ({placeholders})
            """,
            [category_id, *unique_ids],
        )
        self._emit_finance_event(
            event_type="finance.transactions_categorized",
            entity_ref=None,
            payload={
                "count": int(cursor.rowcount),
                "categories": [category_name],
            },
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def list_accounts(self) -> dict[str, object]:
        rows = self.conn.execute(
            "SELECT name, account_type, last_imported_at FROM finance_accounts ORDER BY name"
        ).fetchall()
        return {"accounts": [dict(row) for row in rows]}

    def missing_transaction_ids(self, transaction_ids: list[int]) -> list[int]:
        if not transaction_ids:
            return []

        placeholders = ",".join("?" for _ in transaction_ids)
        rows = self.conn.execute(
            f"SELECT id FROM finance_transactions WHERE id IN ({placeholders})",
            transaction_ids,
        ).fetchall()
        existing = {int(row["id"]) for row in rows}
        return [transaction_id for transaction_id in transaction_ids if transaction_id not in existing]

    def safe_finance_summary(self) -> dict[str, object]:
        return summarize_finances(self.conn)

    def finance_anomalies(self) -> dict[str, object]:
        should_commit_event = not self.conn.in_transaction
        items = find_anomalies(self.conn)
        if items:
            self._emit_finance_event(
                event_type="finance.anomalies_detected",
                entity_ref=None,
                payload={
                    "count": len(items),
                    "total_cents": self._sum_transaction_amount_cents(
                        [
                            int(item["transaction_id"])
                            for item in items
                            if item.get("transaction_id") is not None
                        ]
                    ),
                },
            )
            if should_commit_event:
                self.conn.commit()
        return {"items": items}

    def sensitive_finance_query(self, limit: int = 50, session_ref: str | None = None) -> dict[str, object]:
        if limit < 1 or limit > 500:
            raise InvalidInputError("limit must be between 1 and 500")
        return sensitive_query(self.conn, limit=limit, session_ref=session_ref)

    def generate_weekly_report(self, period_start: str, period_end: str) -> dict[str, object]:
        _validate_weekly_window(period_start, period_end)
        summary = build_weekly_report(self.conn, period_start, period_end)
        summary_payload = summary.to_dict()
        content = render_weekly_markdown(summary, period_start, period_end)
        relative_path = f"Finance/weekly-{period_start}.md"
        planned_path = self.vault_writer.resolve_path(relative_path)
        upsert_report_run(
            self.conn,
            "weekly",
            period_start,
            period_end,
            str(planned_path),
            summary_payload,
            status="pending",
        )
        path: Path | None = None
        try:
            path = self.vault_writer.write_markdown(relative_path, content)
            self._emit_finance_event(
                event_type="finance.report_generated",
                entity_ref=str(path),
                payload={
                    "report_type": "weekly",
                    "period_start": period_start,
                    "period_end": period_end,
                    "vault_path": str(path),
                },
            )
            persist_report_run(
                self.conn,
                "weekly",
                period_start,
                period_end,
                str(path),
                summary_payload,
            )
        except Exception as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            failed_path = path or planned_path
            _best_effort_unlink(failed_path)
            upsert_report_run(
                self.conn,
                "weekly",
                period_start,
                period_end,
                str(failed_path),
                summary_payload,
                status="failed",
                error_message=str(exc),
            )
            raise
        return {"vault_path": str(path), "summary": summary_payload}

    def generate_monthly_report(self, period_start: str, period_end: str) -> dict[str, object]:
        _validate_monthly_window(period_start, period_end)
        summary = build_monthly_report(self.conn, period_start, period_end)
        summary_payload = summary.to_dict()
        content = render_monthly_markdown(summary, period_start, period_end)
        relative_path = f"Finance/monthly-{period_start[:7]}.md"
        planned_path = self.vault_writer.resolve_path(relative_path)
        upsert_report_run(
            self.conn,
            "monthly",
            period_start,
            period_end,
            str(planned_path),
            summary_payload,
            status="pending",
        )
        path: Path | None = None
        try:
            path = self.vault_writer.write_markdown(relative_path, content)
            self._emit_finance_event(
                event_type="finance.report_generated",
                entity_ref=str(path),
                payload={
                    "report_type": "monthly",
                    "period_start": period_start,
                    "period_end": period_end,
                    "vault_path": str(path),
                },
            )
            persist_report_run(
                self.conn,
                "monthly",
                period_start,
                period_end,
                str(path),
                summary_payload,
            )
        except Exception as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            failed_path = path or planned_path
            _best_effort_unlink(failed_path)
            upsert_report_run(
                self.conn,
                "monthly",
                period_start,
                period_end,
                str(failed_path),
                summary_payload,
                status="failed",
                error_message=str(exc),
            )
            raise
        return {"vault_path": str(path), "summary": summary_payload}

    def get_job(self, job_id: str) -> dict[str, object | None]:
        job = get_job(self.conn, job_id)
        if job is None:
            raise NotFoundError(f"Unknown finance job id: {job_id}")
        return job

    def _account_id(self, account_name: str) -> int:
        return int(self._account(account_name)["id"])

    def _account(self, account_name: str):
        row = self.conn.execute(
            "SELECT id, import_profile FROM finance_accounts WHERE name = ?",
            (account_name,),
        ).fetchone()
        if not row:
            raise NotFoundError(f"Unknown finance account: {account_name}")
        return row

    def _insert_batch(self, account_id: int, parsed: ParsedImportBatch) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO finance_import_batches (account_id, source_type, source_ref, raw_fingerprint)
            VALUES (?, ?, ?, ?)
            """,
            (
                account_id,
                parsed.source_type,
                parsed.source_ref,
                parsed.raw_fingerprint,
            ),
        )
        return int(cursor.lastrowid)

    def _insert_transaction(
        self,
        account_id: int,
        batch_id: int,
        txn: ParsedTransaction,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO finance_transactions (
                account_id, batch_id, posted_at, description, merchant, amount_cents,
                category_id, category_source, external_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'uncategorized', ?)
            """,
            (
                account_id,
                batch_id,
                txn.posted_at,
                txn.description,
                txn.merchant,
                txn.amount_cents,
                self._uncategorized_id(),
                txn.external_id,
            ),
        )
        return int(cursor.lastrowid)

    def _category_id(self, category_name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM finance_categories WHERE name = ?",
            (category_name,),
        ).fetchone()
        if not row:
            raise NotFoundError(f"Unknown finance category: {category_name}")
        return int(row["id"])

    def _uncategorized_id(self) -> int:
        if self._uncategorized_category_id is None:
            self._uncategorized_category_id = self._category_id("Uncategorized")
        return self._uncategorized_category_id

    def _emit_finance_event(
        self,
        *,
        event_type: str,
        entity_ref: str | None,
        payload: dict[str, object],
    ) -> int | None:
        return emit_event(
            self.conn,
            event_type=event_type,
            domain="finance",
            occurred_at=_utc_now_isoformat(),
            entity_ref=entity_ref,
            source=EVENT_SOURCE,
            payload=payload,
        )

    def _sum_transaction_amount_cents(self, transaction_ids: list[int]) -> int:
        unique_ids = list(dict.fromkeys(transaction_ids))
        if not unique_ids:
            return 0

        placeholders = ",".join("?" for _ in unique_ids)
        row = self.conn.execute(
            f"""
            SELECT COALESCE(SUM(amount_cents), 0) AS total_cents
            FROM finance_transactions
            WHERE id IN ({placeholders})
            """,
            unique_ids,
        ).fetchone()
        return int(row["total_cents"])


def _parse_date_window(period_start: str, period_end: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError as exc:
        raise InvalidInputError("Invalid ISO date") from exc
    if start > end:
        raise InvalidInputError("period_start must be on or before period_end")
    return start, end


def _validate_weekly_window(period_start: str, period_end: str) -> None:
    start, end = _parse_date_window(period_start, period_end)
    if (end - start).days != 6:
        raise InvalidInputError("weekly reports must span exactly 7 days")


def _validate_monthly_window(period_start: str, period_end: str) -> None:
    start, end = _parse_date_window(period_start, period_end)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    expected_end = next_month - timedelta(days=1)
    if start.day != 1 or end != expected_end:
        raise InvalidInputError("monthly reports must cover a full calendar month")


def _canonicalize_existing_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_absolute():
        return resolved

    current = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        actual_part = part
        try:
            for child in current.iterdir():
                if child.name.casefold() == part.casefold():
                    actual_part = child.name
                    break
        except OSError:
            pass
        current = current / actual_part
    return current


def _utc_now_isoformat() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _best_effort_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        logger.warning("Unable to remove failed report artifact %s: %s", path, exc)
