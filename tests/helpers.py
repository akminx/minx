from __future__ import annotations
import json
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


class MealsSeeder:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def meal_entry(
        self,
        *,
        occurred_at: str = "2026-04-12T12:00:00Z",
        meal_kind: str = "lunch",
        summary: str | None = "Test meal",
        food_items: list[dict[str, object]] | None = None,
        protein_grams: float | None = None,
        calories: int | None = None,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO meals_meal_entries (
                occurred_at, meal_kind, summary, food_items_json,
                protein_grams, calories, source
            ) VALUES (?, ?, ?, ?, ?, ?, 'test')
            """,
            (
                occurred_at,
                meal_kind,
                summary,
                json.dumps(food_items or []),
                protein_grams,
                calories,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def pantry_item(
        self,
        *,
        display_name: str,
        normalized_name: str | None = None,
        quantity: float | None = None,
        unit: str | None = None,
        expiration_date: str | None = None,
        low_stock_threshold: float | None = None,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO meals_pantry_items (
                display_name, normalized_name, quantity, unit,
                expiration_date, low_stock_threshold, source
            ) VALUES (?, ?, ?, ?, ?, ?, 'test')
            """,
            (
                display_name,
                normalized_name or display_name.lower().strip(),
                quantity,
                unit,
                expiration_date,
                low_stock_threshold,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def recipe(
        self,
        *,
        vault_path: str,
        title: str,
        content_hash: str = "abc123",
        tags: list[str] | None = None,
        image_ref: str | None = None,
        source_url: str | None = None,
        nutrition_summary: dict[str, object] | None = None,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO meals_recipes (
                vault_path, title, normalized_title, source_url,
                image_ref, tags_json, nutrition_summary_json, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vault_path,
                title,
                title.lower().strip(),
                source_url,
                image_ref,
                json.dumps(tags or []),
                json.dumps(nutrition_summary) if nutrition_summary is not None else None,
                content_hash,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def recipe_ingredient(
        self,
        *,
        recipe_id: int,
        display_text: str,
        normalized_name: str,
        quantity: float | None = None,
        unit: str | None = None,
        is_required: bool = True,
        sort_order: int = 0,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO meals_recipe_ingredients (
                recipe_id, display_text, normalized_name,
                quantity, unit, is_required, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (recipe_id, display_text, normalized_name, quantity, unit, int(is_required), sort_order),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def substitution(
        self,
        *,
        recipe_ingredient_id: int,
        substitute_normalized_name: str,
        display_text: str,
        priority: int = 0,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO meals_recipe_substitutions (
                recipe_ingredient_id, substitute_normalized_name, display_text, priority
            ) VALUES (?, ?, ?, ?)
            """,
            (recipe_ingredient_id, substitute_normalized_name, display_text, priority),
        )
        self._conn.commit()
        return cursor.lastrowid or 0
