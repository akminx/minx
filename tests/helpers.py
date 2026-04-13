from __future__ import annotations
from sqlite3 import Connection
from minx_mcp.core.goals import GoalService
from minx_mcp.core.models import GoalCreateInput

class FinanceSeeder:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._next_batch_id = 1

    def batch(self, *, account_name: str = "DCU") -> int:
        account_id = self._conn.execute(
            "SELECT id FROM finance_accounts WHERE name = ?", (account_name,)
        ).fetchone()["id"]
        batch_id = self._next_batch_id
        self._next_batch_id += 1
        self._conn.execute(
            """
            INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
            VALUES (?, ?, 'csv', 'seed.csv', ?)
            """,
            (batch_id, account_id, f"fp-{batch_id}"),
        )
        return batch_id

    def transaction(
        self,
        *,
        posted_at: str,
        amount_cents: int,
        merchant: str = "Test Merchant",
        description: str = "Test transaction",
        category_name: str | None = None,
        account_name: str = "DCU",
        batch_id: int | None = None,
    ) -> int:
        if batch_id is None:
            batch_id = self.batch(account_name=account_name)
        account_id = self._conn.execute(
            "SELECT id FROM finance_accounts WHERE name = ?", (account_name,)
        ).fetchone()["id"]
        category_id = None
        if category_name is not None:
            category_id = self._conn.execute(
                "SELECT id FROM finance_categories WHERE name = ?", (category_name,)
            ).fetchone()["id"]
        cursor = self._conn.execute(
            """
            INSERT INTO finance_transactions (
                account_id, batch_id, posted_at, description, merchant, amount_cents,
                category_id, category_source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'manual')
            """,
            (account_id, batch_id, posted_at, description, merchant, amount_cents, category_id),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def goal(
        self,
        *,
        title: str,
        metric_type: str = "sum_below",
        target_value: int = 10_000,
        period: str = "monthly",
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
        starts_on: str = "2026-01-01",
        ends_on: str | None = None,
    ) -> int:
        svc = GoalService(self._conn)
        record = svc.create_goal(GoalCreateInput(
            goal_type="spending_cap",
            title=title,
            metric_type=metric_type,
            target_value=target_value,
            period=period,
            domain="finance",
            category_names=category_names or [],
            merchant_names=merchant_names or [],
            account_names=account_names or [],
            starts_on=starts_on,
            ends_on=ends_on,
            notes=None,
        ))
        return record.id

    def category_id(self, name: str) -> int:
        return self._conn.execute(
            "SELECT id FROM finance_categories WHERE name = ?", (name,)
        ).fetchone()["id"]

    def account_id(self, name: str = "DCU") -> int:
        return self._conn.execute(
            "SELECT id FROM finance_accounts WHERE name = ?", (name,)
        ).fetchone()["id"]
