from __future__ import annotations

import hashlib
from pathlib import Path
from sqlite3 import Connection
from string import Template

from minx_mcp.finance.analytics import find_anomalies, sensitive_query, summarize_finances
from minx_mcp.finance.dedupe import fingerprint_transaction
from minx_mcp.finance.importers import parse_source_file
from minx_mcp.finance.reports import (
    build_monthly_report,
    build_weekly_report,
    persist_report_run,
)
from minx_mcp.jobs import get_job, mark_completed, mark_failed, mark_running, submit_job
from minx_mcp.vault_writer import VaultWriter


class FinanceService:
    def __init__(self, conn: Connection, vault_root: Path) -> None:
        self.conn = conn
        self.vault_writer = VaultWriter(vault_root, ("Finance",))

    def finance_import(
        self,
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]:
        idempotency_key = hashlib.sha256(f"{account_name}|{source_ref}".encode()).hexdigest()
        job = submit_job(self.conn, "finance_import", "system", source_ref, idempotency_key)
        if job["status"] == "completed":
            return {"job_id": job["id"], "status": job["status"], "result": job["result"]}

        try:
            mark_running(self.conn, str(job["id"]))
            self.conn.execute("SAVEPOINT finance_import")

            parsed = parse_source_file(Path(source_ref), account_name, source_kind, mapping)
            account_id = self._account_id(account_name)
            batch_id = self._insert_batch(account_id, parsed)
            inserted = 0
            skipped = 0

            for txn in parsed["transactions"]:
                fingerprint = fingerprint_transaction(account_name, txn)
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
            self.conn.commit()

            result = {"batch_id": batch_id, "inserted": inserted, "skipped": skipped}
            mark_completed(self.conn, str(job["id"]), result)
            return {"job_id": job["id"], "status": "completed", "result": result}
        except Exception as exc:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK TO SAVEPOINT finance_import")
                self.conn.execute("RELEASE SAVEPOINT finance_import")
                self.conn.commit()
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
                WHERE merchant LIKE ?
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

    def safe_finance_summary(self) -> dict[str, object]:
        return summarize_finances(self.conn)

    def finance_anomalies(self) -> dict[str, object]:
        return find_anomalies(self.conn)

    def sensitive_finance_query(self, limit: int = 50, session_ref: str | None = None) -> dict[str, object]:
        return sensitive_query(self.conn, limit=limit, session_ref=session_ref)

    def generate_weekly_report(self, period_start: str, period_end: str) -> dict[str, object]:
        summary = build_weekly_report(self.conn, period_start, period_end)
        template = Template(self._template_path("finance-weekly-summary.md").read_text())
        category_lines = "\n".join(
            f"- {item['category_name']}: {item['total_amount']}"
            for item in summary["categories"]
        ) or "- No transactions"
        content = template.substitute(
            period_start=period_start,
            period_end=period_end,
            category_lines=category_lines,
        )
        relative_path = f"Finance/weekly-{period_start}.md"
        path = self.vault_writer.write_markdown(relative_path, content)
        persist_report_run(self.conn, "weekly", period_start, period_end, str(path), summary)
        return {"vault_path": str(path), "summary": summary}

    def generate_monthly_report(self, period_start: str, period_end: str) -> dict[str, object]:
        summary = build_monthly_report(self.conn, period_start, period_end)
        template = Template(self._template_path("finance-monthly-summary.md").read_text())
        account_lines = "\n".join(
            f"- {item['account_name']}: {item['total_amount']}"
            for item in summary["accounts"]
        ) or "- No transactions"
        content = template.substitute(
            period_start=period_start,
            period_end=period_end,
            account_lines=account_lines,
        )
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
                account_id, batch_id, posted_at, description, merchant, amount, category_id, external_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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

    def _template_path(self, name: str) -> Path:
        return Path(__file__).resolve().parents[2] / "templates" / name
