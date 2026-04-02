from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from sqlite3 import Connection

from minx_mcp.db import get_connection
from minx_mcp.finance.analytics import find_anomalies, sensitive_query, summarize_finances
from minx_mcp.finance.dedupe import fingerprint_transaction
from minx_mcp.finance.importers import parse_source_file
from minx_mcp.finance.reports import (
    build_monthly_report,
    build_weekly_report,
    persist_report_run,
    render_monthly_markdown,
    render_weekly_markdown,
)
from minx_mcp.jobs import get_job, mark_completed, mark_failed, mark_running, submit_job
from minx_mcp.vault_writer import VaultWriter


class FinanceService:
    def __init__(self, db_path: Path, vault_root: Path) -> None:
        self._db_path = db_path
        self._local = threading.local()
        self.vault_writer = VaultWriter(vault_root, ("Finance",))

    @property
    def conn(self) -> Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = get_connection(self._db_path)
            self._local.conn = conn
        return conn

    def finance_import(
        self,
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]:
        content_hash = hashlib.sha256(Path(source_ref).read_bytes()).hexdigest()
        idempotency_key = hashlib.sha256(f"{account_name}|{source_ref}|{content_hash}".encode()).hexdigest()
        job = submit_job(self.conn, "finance_import", "system", source_ref, idempotency_key)
        if job["status"] == "completed":
            return {"job_id": job["id"], "status": job["status"], "result": job["result"]}

        try:
            mark_running(self.conn, str(job["id"]), commit=False)
            self.conn.execute("SAVEPOINT finance_import")

            parsed = parse_source_file(Path(source_ref), account_name, source_kind, mapping)
            account_id = self._account_id(account_name)
            batch_id = self._insert_batch(account_id, parsed)
            inserted = 0
            skipped = 0

            for txn in parsed["transactions"]:
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

            self.conn.execute(
                "UPDATE finance_import_batches SET inserted_count = ?, skipped_count = ? WHERE id = ?",
                (inserted, skipped, batch_id),
            )
            self.conn.execute(
                "UPDATE finance_accounts SET last_imported_at = datetime('now') WHERE id = ?",
                (account_id,),
            )
            self.apply_category_rules(commit=False)
            self.conn.execute("RELEASE SAVEPOINT finance_import")

            result = {"batch_id": batch_id, "inserted": inserted, "skipped": skipped}
            mark_completed(self.conn, str(job["id"]), result)
            return {"job_id": job["id"], "status": "completed", "result": result}
        except Exception as exc:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK TO SAVEPOINT finance_import")
                self.conn.execute("RELEASE SAVEPOINT finance_import")
            mark_failed(self.conn, str(job["id"]), str(exc))
            raise

    def add_category_rule(self, category_name: str, match_kind: str, pattern: str) -> None:
        category_id = self.conn.execute(
            "SELECT id FROM finance_categories WHERE name = ?",
            (category_name,),
        ).fetchone()["id"]
        self.conn.execute(
            """
            INSERT INTO finance_category_rules (category_id, match_kind, pattern)
            VALUES (?, ?, ?)
            """,
            (category_id, match_kind, pattern),
        )
        self.conn.commit()

    def apply_category_rules(self, *, commit: bool = True) -> None:
        rules = self.conn.execute(
            """
            SELECT r.pattern, r.match_kind, r.category_id
            FROM finance_category_rules r
            ORDER BY r.priority ASC, r.id ASC
            """
        ).fetchall()
        for rule in rules:
            if rule["match_kind"] != "merchant_contains":
                continue
            self.conn.execute(
                """
                UPDATE finance_transactions
                SET category_id = ?, category_source = 'rule'
                WHERE merchant LIKE ? AND category_source != 'manual'
                """,
                (rule["category_id"], f"%{rule['pattern']}%"),
            )
        if commit:
            self.conn.commit()

    def finance_categorize(self, transaction_ids: list[int], category_name: str) -> None:
        category_id = self.conn.execute(
            "SELECT id FROM finance_categories WHERE name = ?",
            (category_name,),
        ).fetchone()["id"]
        placeholders = ",".join("?" for _ in transaction_ids)
        self.conn.execute(
            f"""
            UPDATE finance_transactions
            SET category_id = ?, category_source = 'manual'
            WHERE id IN ({placeholders})
            """,
            [category_id, *transaction_ids],
        )
        self.conn.commit()

    def list_accounts(self) -> dict[str, object]:
        rows = self.conn.execute(
            "SELECT name, account_type, last_imported_at FROM finance_accounts ORDER BY name"
        ).fetchall()
        return {"accounts": [dict(row) for row in rows]}

    def safe_finance_summary(self) -> dict[str, object]:
        return summarize_finances(self.conn)

    def finance_anomalies(self) -> dict[str, object]:
        return {"items": find_anomalies(self.conn)}

    def sensitive_finance_query(self, limit: int = 50, session_ref: str | None = None) -> dict[str, object]:
        return sensitive_query(self.conn, limit=limit, session_ref=session_ref)

    def generate_weekly_report(self, period_start: str, period_end: str) -> dict[str, object]:
        summary = build_weekly_report(self.conn, period_start, period_end)
        content = render_weekly_markdown(summary, period_start, period_end)
        relative_path = f"Finance/weekly-{period_start}.md"
        path = self.vault_writer.write_markdown(relative_path, content)
        persist_report_run(self.conn, "weekly", period_start, period_end, str(path), summary)
        return {"vault_path": str(path), "summary": summary}

    def generate_monthly_report(self, period_start: str, period_end: str) -> dict[str, object]:
        summary = build_monthly_report(self.conn, period_start, period_end)
        content = render_monthly_markdown(summary, period_start, period_end)
        relative_path = f"Finance/monthly-{period_start[:7]}.md"
        path = self.vault_writer.write_markdown(relative_path, content)
        persist_report_run(self.conn, "monthly", period_start, period_end, str(path), summary)
        return {"vault_path": str(path), "summary": summary}

    def get_job(self, job_id: str) -> dict[str, object | None] | None:
        return get_job(self.conn, job_id)

    def _account_id(self, account_name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM finance_accounts WHERE name = ?",
            (account_name,),
        ).fetchone()
        if not row:
            raise ValueError(f"Unknown finance account: {account_name}")
        return int(row["id"])

    def _insert_batch(self, account_id: int, parsed: dict[str, object]) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO finance_import_batches (account_id, source_type, source_ref, raw_fingerprint)
            VALUES (?, ?, ?, ?)
            """,
            (
                account_id,
                parsed["source_type"],
                parsed["source_ref"],
                parsed["raw_fingerprint"],
            ),
        )
        return int(cursor.lastrowid)

    def _insert_transaction(
        self,
        account_id: int,
        batch_id: int,
        txn: dict[str, object],
    ) -> int:
        uncategorized_id = self.conn.execute(
            "SELECT id FROM finance_categories WHERE name = 'Uncategorized'"
        ).fetchone()["id"]
        cursor = self.conn.execute(
            """
            INSERT INTO finance_transactions (
                account_id, batch_id, posted_at, description, merchant, amount,
                category_id, category_source, external_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'uncategorized', ?)
            """,
            (
                account_id,
                batch_id,
                txn["posted_at"],
                txn["description"],
                txn["merchant"],
                txn["amount"],
                uncategorized_id,
                txn.get("external_id"),
            ),
        )
        return int(cursor.lastrowid)

