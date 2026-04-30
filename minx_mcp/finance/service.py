from __future__ import annotations

import logging
from pathlib import Path
from sqlite3 import Row
from typing import cast

from minx_mcp.base_service import BaseService
from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.events import emit_event
from minx_mcp.finance.analytics import (
    build_finance_monitoring,
    find_anomalies,
    sensitive_query,
    sensitive_query_count,
    sensitive_query_total_cents,
    summarize_finances,
)
from minx_mcp.finance.import_models import ParsedImportBatch, ParsedTransaction
from minx_mcp.finance.import_workflow import preview_finance_import, run_finance_import
from minx_mcp.finance.normalization import normalize_merchant
from minx_mcp.finance.report_orchestration import run_monthly_report, run_weekly_report
from minx_mcp.finance.rules import Rule, apply_rules
from minx_mcp.jobs import get_job
from minx_mcp.time_utils import utc_now_isoformat
from minx_mcp.vault_writer import VaultWriter

MAX_SENSITIVE_QUERY_LIMIT = 500

EVENT_SOURCE = "finance.service"
logger = logging.getLogger(__name__)


class FinanceService(BaseService):
    def __init__(self, db_path: Path, vault_root: Path, import_root: Path | None = None) -> None:
        super().__init__(db_path)
        self.import_root = (import_root or db_path.parent).resolve()
        self.vault_writer = VaultWriter(vault_root, ("Finance",))

    def finance_import(
        self,
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return run_finance_import(self, source_ref, account_name, source_kind, mapping)

    def finance_import_preview(
        self,
        source_ref: str,
        account_name: str,
        source_kind: str | None = None,
        mapping: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return preview_finance_import(self, source_ref, account_name, source_kind, mapping)

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
        uncategorized_id = self._uncategorized_id()
        reset_where_clause = "WHERE category_source = 'rule'"
        reset_params: list[object] = []
        if batch_id is not None:
            reset_where_clause += " AND batch_id = ?"
            reset_params.append(batch_id)
        # Safe: reset_where_clause is fixed literals plus optional batch_id ?; category id bound in params.
        self.conn.execute(
            f"""
            UPDATE finance_transactions
            SET category_id = ?, category_source = 'uncategorized'
            {reset_where_clause}
            """,  # noqa: S608
            [uncategorized_id, *reset_params],
        )

        stored_rules = self.conn.execute(
            """
            SELECT r.pattern, r.match_kind, r.category_id, r.priority
            FROM finance_category_rules r
            ORDER BY r.priority ASC, r.id ASC
            """
        ).fetchall()
        rules: list[Rule] = []
        for rule in stored_rules:
            match_kind = str(rule["match_kind"])
            if match_kind != "merchant_contains":
                logger.warning(
                    "skipping unhandled finance category rule match_kind=%s",
                    match_kind,
                )
                continue
            rules.append(
                Rule(
                    stage="categorize",
                    priority=int(rule["priority"]),
                    kind="categorize_merchant",
                    match=str(rule["pattern"]),
                    value=str(rule["category_id"]),
                )
            )
        if not rules:
            if commit:
                self.conn.commit()
            return

        where_clause = "WHERE category_source != 'manual'"
        params: list[object] = []
        if batch_id is not None:
            where_clause += " AND batch_id = ?"
            params.append(batch_id)

        # Safe: where_clause is fixed predicate text plus optional batch_id ?; values bound in params.
        rows = self.conn.execute(
            f"""
            SELECT id, merchant, raw_merchant
            FROM finance_transactions
            {where_clause}
            ORDER BY id ASC
            """,  # noqa: S608
            params,
        ).fetchall()
        for row in rows:
            applied = apply_rules(
                {
                    "merchant": row["merchant"],
                    "raw_merchant": row["raw_merchant"],
                    "category_name": None,
                },
                rules,
            )
            category_id = applied.get("category_name")
            if not isinstance(category_id, str):
                continue
            self.conn.execute(
                """
                UPDATE finance_transactions
                SET category_id = ?, category_source = 'rule'
                WHERE id = ?
                """,
                (int(category_id), int(row["id"])),
            )
        if commit:
            self.conn.commit()

    def finance_categorize(self, transaction_ids: list[int], category_name: str) -> int:
        if not transaction_ids:
            raise InvalidInputError("transaction_ids must be a non-empty list")
        unique_ids = list(dict.fromkeys(transaction_ids))
        category_id = self._category_id(category_name)
        placeholders = ",".join("?" for _ in unique_ids)
        savepoint = "finance_categorize"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            # Safe: IN expands to len(unique_ids) "?" tokens; integer ids are bound, not concatenated.
            cursor = self.conn.execute(
                f"""
                UPDATE finance_transactions
                SET category_id = ?, category_source = 'manual'
                WHERE id IN ({placeholders})
                """,  # noqa: S608
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
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        self.conn.commit()
        return int(cursor.rowcount)

    def list_accounts(self) -> dict[str, object]:
        rows = self.conn.execute(
            "SELECT name, account_type, last_imported_at FROM finance_accounts ORDER BY name"
        ).fetchall()
        return {"accounts": [dict(row) for row in rows]}

    def list_account_names(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM finance_accounts ORDER BY name ASC").fetchall()
        return [str(row["name"]) for row in rows]

    def list_transaction_category_names(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM finance_categories ORDER BY name ASC").fetchall()
        return [str(row["name"]) for row in rows]

    def list_spending_merchant_names(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT merchant
            FROM finance_transactions
            WHERE amount_cents < 0
              AND COALESCE(TRIM(merchant), '') != ''
            ORDER BY merchant ASC
            """
        ).fetchall()
        return [str(row["merchant"]) for row in rows]

    def missing_transaction_ids(self, transaction_ids: list[int]) -> list[int]:
        if not transaction_ids:
            return []

        placeholders = ",".join("?" for _ in transaction_ids)
        # Safe: placeholder count matches transaction_ids; ids are bound as parameters.
        rows = self.conn.execute(
            f"SELECT id FROM finance_transactions WHERE id IN ({placeholders})",  # noqa: S608
            transaction_ids,
        ).fetchall()
        existing = {int(row["id"]) for row in rows}
        return [
            transaction_id for transaction_id in transaction_ids if transaction_id not in existing
        ]

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
                            transaction_id
                            for item in items
                            if isinstance(
                                (transaction_id := item.get("transaction_id")),
                                int,
                            )
                        ]
                    ),
                },
            )
            if should_commit_event:
                self.conn.commit()
        return {"items": items}

    def finance_monitoring(self, *, period_start: str, period_end: str) -> dict[str, object]:
        return build_finance_monitoring(
            self.conn,
            period_start=period_start,
            period_end=period_end,
        )

    def sensitive_finance_query(
        self,
        limit: int = 50,
        session_ref: str | None = None,
        audit_tool_name: str = "sensitive_finance_query",
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        category_name: str | None = None,
        merchant: str | None = None,
        account_name: str | None = None,
        description_contains: str | None = None,
    ) -> dict[str, object]:
        if limit < 1 or limit > MAX_SENSITIVE_QUERY_LIMIT:
            raise InvalidInputError("limit must be between 1 and 500")
        return sensitive_query(
            self.conn,
            limit=limit,
            session_ref=session_ref,
            audit_tool_name=audit_tool_name,
            start_date=start_date,
            end_date=end_date,
            category_name=category_name,
            merchant=merchant,
            account_name=account_name,
            description_contains=description_contains,
        )

    def get_filtered_spending_total(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        category_name: str | None = None,
        merchant: str | None = None,
        account_name: str | None = None,
        description_contains: str | None = None,
        session_ref: str | None = None,
    ) -> int:
        return sensitive_query_total_cents(
            self.conn,
            start_date=start_date,
            end_date=end_date,
            category_name=category_name,
            merchant=merchant,
            account_name=account_name,
            description_contains=description_contains,
            session_ref=session_ref,
        )

    def get_filtered_transaction_count(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        category_name: str | None = None,
        merchant: str | None = None,
        account_name: str | None = None,
        description_contains: str | None = None,
        session_ref: str | None = None,
    ) -> int:
        return sensitive_query_count(
            self.conn,
            start_date=start_date,
            end_date=end_date,
            category_name=category_name,
            merchant=merchant,
            account_name=account_name,
            description_contains=description_contains,
            session_ref=session_ref,
        )

    def generate_weekly_report(self, period_start: str, period_end: str) -> dict[str, object]:
        return run_weekly_report(self, period_start, period_end)

    def generate_monthly_report(self, period_start: str, period_end: str) -> dict[str, object]:
        return run_monthly_report(self, period_start, period_end)

    def get_job(self, job_id: str) -> dict[str, object | None]:
        job = get_job(self.conn, job_id)
        if job is None:
            raise NotFoundError(f"Unknown finance job id: {job_id}")
        return job

    def _account_id(self, account_name: str) -> int:
        return int(self._account(account_name)["id"])

    def _account(self, account_name: str) -> Row:
        row = self.conn.execute(
            "SELECT id, import_profile FROM finance_accounts WHERE name = ?",
            (account_name,),
        ).fetchone()
        if not row:
            raise NotFoundError(f"Unknown finance account: {account_name}")
        return cast(Row, row)

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
        if cursor.lastrowid is None:
            raise RuntimeError("finance_import_batches insert did not return a row id")
        return int(cursor.lastrowid)

    def _insert_transaction(
        self,
        account_id: int,
        batch_id: int,
        txn: ParsedTransaction,
    ) -> int:
        category_id = self._best_effort_category_id(txn.category_hint)
        category_source = "import" if category_id is not None else "uncategorized"
        if category_id is None:
            category_id = self._uncategorized_id()
        raw_merchant = txn.merchant
        merchant = normalize_merchant(raw_merchant)
        cursor = self.conn.execute(
            """
            INSERT INTO finance_transactions (
                account_id, batch_id, posted_at, description, merchant, raw_merchant, amount_cents,
                category_id, category_source, external_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                batch_id,
                txn.posted_at,
                txn.description,
                merchant,
                raw_merchant,
                txn.amount_cents,
                category_id,
                category_source,
                txn.external_id,
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("finance_transactions insert did not return a row id")
        return int(cursor.lastrowid)

    def _category_id(self, category_name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM finance_categories WHERE name = ?",
            (category_name,),
        ).fetchone()
        if not row:
            raise NotFoundError(f"Unknown finance category: {category_name}")
        return int(row["id"])

    def _best_effort_category_id(self, category_hint: str | None) -> int | None:
        if not category_hint:
            return None
        return self._category_lookup_by_normalized_name().get(_normalize_category_name(category_hint))

    def _category_lookup_by_normalized_name(self) -> dict[str, int]:
        cached = getattr(self._local, "category_lookup_by_normalized_name", None)
        if cached is None:
            rows = self.conn.execute(
                "SELECT id, name FROM finance_categories ORDER BY name ASC"
            ).fetchall()
            cached = {}
            for row in rows:
                key = _normalize_category_name(str(row["name"]))
                if key not in cached:
                    cached[key] = int(row["id"])
            self._local.category_lookup_by_normalized_name = cached
        return cached

    def _uncategorized_id(self) -> int:
        cached = getattr(self._local, "uncategorized_category_id", None)
        if cached is None:
            cached = self._category_id("Uncategorized")
            self._local.uncategorized_category_id = cached
        return cached

    def _emit_finance_event(
        self,
        *,
        event_type: str,
        entity_ref: str | None,
        payload: dict[str, object],
    ) -> int:
        event_id = emit_event(
            self.conn,
            event_type=event_type,
            domain="finance",
            occurred_at=utc_now_isoformat(),
            entity_ref=entity_ref,
            source=EVENT_SOURCE,
            payload=payload,
        )
        if event_id is None:
            raise RuntimeError(f"{event_type} event emission failed")
        return event_id

    def _sum_transaction_amount_cents(self, transaction_ids: list[int]) -> int:
        unique_ids = list(dict.fromkeys(transaction_ids))
        if not unique_ids:
            return 0

        placeholders = ",".join("?" for _ in unique_ids)
        # Safe: IN list is only "?"; transaction id integers are bound via unique_ids.
        row = self.conn.execute(
            f"""
            SELECT COALESCE(SUM(amount_cents), 0) AS total_cents
            FROM finance_transactions
            WHERE id IN ({placeholders})
            """,  # noqa: S608
            unique_ids,
        ).fetchone()
        return int(row["total_cents"])


def _normalize_category_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())
