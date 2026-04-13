# Slice 3: Meals MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Meals MCP domain server with meal logging, pantry management, Obsidian recipe indexing, and deterministic pantry-aware recipe recommendation.

**Architecture:** Meals is a first-class MCP domain parallel to Finance. It owns meal logs, pantry state, and recipe metadata in SQLite. Core composes a NutritionSnapshot from the Meals read API and runs deterministic nutrition detectors. Recipe recommendation is a deterministic ranking algorithm in the Meals domain, not in Core.

**Tech Stack:** Python 3.12, FastMCP, Pydantic v2, SQLite3, pytest, mypy

**Spec:** `docs/superpowers/specs/2026-04-11-slice3-meals-mcp-design.md`

**Design decisions resolved:**
- Recipe storage: normalized into `meals_recipes` + `meals_recipe_ingredients` tables (not indexed-only)
- Pantry canonical source: SQLite only (Obsidian projection deferred)
- Substitutions: static per-recipe (ingredient-class learning deferred)
- `NutritionSnapshot`: discrete field on `DailySnapshot` (generic domain contribution container deferred)

---

## File Structure

### New files to create

| File | Responsibility |
|------|---------------|
| `minx_mcp/meals/__init__.py` | Package init with `__version__` |
| `minx_mcp/meals/__main__.py` | Entry point (mirrors `finance/__main__.py`) |
| `minx_mcp/meals/server.py` | FastMCP tool definitions, input validation |
| `minx_mcp/meals/service.py` | Domain write operations, connection management |
| `minx_mcp/meals/read_api.py` | Read-only Core-facing interface (conforms to `MealsReadInterface` Protocol) |
| `minx_mcp/meals/models.py` | Domain dataclasses: meal entries, pantry items, recipes, recommendations |
| `minx_mcp/meals/recipes.py` | Obsidian recipe markdown parsing and normalization |
| `minx_mcp/meals/pantry.py` | Ingredient normalization, pantry matching, substitution resolution |
| `minx_mcp/meals/recommendations.py` | Availability classification, ranking, recommendation output |
| `minx_mcp/schema/migrations/010_meals.sql` | Meals domain schema |
| `schema/migrations/010_meals.sql` | Mirror copy (tests enforce matching content) |
| `tests/test_meals_service.py` | Service layer tests |
| `tests/test_meals_events.py` | Event registration and emission tests |
| `tests/test_meals_recipes.py` | Recipe parsing tests |
| `tests/test_meals_pantry.py` | Pantry matching and normalization tests |
| `tests/test_meals_recommendations.py` | Classification, ranking, recommendation tests |
| `tests/test_meals_read_api.py` | Read API and Core integration tests |
| `tests/test_meals_server.py` | MCP tool tests (in-memory `call_tool`) |
| `tests/test_nutrition_detectors.py` | Nutrition detector tests |

### Existing files to modify

| File | Changes |
|------|---------|
| `minx_mcp/core/events.py` | Refactor `PAYLOAD_MODELS` to compose per-domain declarations; add Meals event payloads |
| `minx_mcp/core/models.py` | Add `MealsReadInterface` Protocol, `NutritionSnapshot` dataclass |
| `minx_mcp/core/read_models.py` | Add `NutritionSnapshot` to `ReadModels`; add `_summarize_event` cases for `meal.logged`, `nutrition.day_updated` |
| `minx_mcp/core/detectors.py` | Add nutrition detectors: `nutrition.low_protein`, `nutrition.skipped_meals` |
| `minx_mcp/core/snapshot.py` | Pass `meals_api` through to read model assembly |
| `minx_mcp/core/__init__.py` | Export Meals event payload classes |
| `tests/helpers.py` | Add `MealsSeeder` class |
| `tests/conftest.py` | Add `meals_seeder` fixture |
| `pyproject.toml` | Add `minx-meals` entry point |

---

## Task 1: Event Registry Refactor

Move event payload declarations from a single dict in `core/events.py` to per-domain mappings composed at import time. This must land before Meals adds its events, so Meals doesn't extend the monolithic pattern.

**Files:**
- Modify: `minx_mcp/core/events.py`
- Create: `minx_mcp/finance/events.py`
- Modify: `tests/test_detectors.py` (if any tests reference `PAYLOAD_MODELS` directly)
- Test: `tests/test_meals_events.py` (partial — just the registry test)

- [ ] **Step 1: Write failing test — per-domain event composition**

In `tests/test_meals_events.py`:

```python
from minx_mcp.core.events import PAYLOAD_MODELS


def test_payload_models_includes_finance_events():
    assert "finance.transactions_imported" in PAYLOAD_MODELS
    assert "finance.transactions_categorized" in PAYLOAD_MODELS
    assert "finance.report_generated" in PAYLOAD_MODELS
    assert "finance.anomalies_detected" in PAYLOAD_MODELS


def test_payload_models_rejects_unknown_event_types():
    assert "meals.nonexistent" not in PAYLOAD_MODELS
```

- [ ] **Step 2: Run test to verify it passes (baseline)**

Run: `.venv/bin/python -m pytest tests/test_meals_events.py -v`
Expected: PASS (this verifies existing behavior before refactor)

- [ ] **Step 3: Extract Finance event declarations to `minx_mcp/finance/events.py`**

Create `minx_mcp/finance/events.py`. Preserve existing type constraints (e.g., `Literal` on `report_type`). Do NOT redeclare `model_config` on subclasses — the base `EventPayload` already sets `extra="forbid"`:

```python
from __future__ import annotations

from typing import Literal

from minx_mcp.core.events import EventPayload


class TransactionsImportedPayload(EventPayload):
    account_name: str
    account_id: int
    job_id: str
    transaction_count: int
    total_cents: int
    source_kind: str


class TransactionsCategorizedPayload(EventPayload):
    count: int
    categories: list[str]


class ReportGeneratedPayload(EventPayload):
    report_type: Literal["weekly", "monthly"]
    period_start: str
    period_end: str
    vault_path: str


class AnomaliesDetectedPayload(EventPayload):
    count: int
    total_cents: int


FINANCE_EVENT_PAYLOADS: dict[str, type[EventPayload]] = {
    "finance.transactions_imported": TransactionsImportedPayload,
    "finance.transactions_categorized": TransactionsCategorizedPayload,
    "finance.report_generated": ReportGeneratedPayload,
    "finance.anomalies_detected": AnomaliesDetectedPayload,
}
```

- [ ] **Step 4: Update `core/events.py` to compose from domain declarations**

In `minx_mcp/core/events.py`, remove the inline payload model classes and `PAYLOAD_MODELS` dict. Replace with:

```python
from minx_mcp.finance.events import FINANCE_EVENT_PAYLOADS

PAYLOAD_MODELS: dict[str, type[EventPayload]] = {
    **FINANCE_EVENT_PAYLOADS,
}
```

Keep `EventPayload` base class, `Event` dataclass, `emit_event()`, `query_events()`, `PAYLOAD_UPCASTERS`, and all helper functions in `core/events.py`. Only the concrete payload classes and `PAYLOAD_MODELS` entries move.

Update `core/__init__.py` to re-export from the new location:

```python
from minx_mcp.finance.events import (
    AnomaliesDetectedPayload,
    ReportGeneratedPayload,
    TransactionsCategorizedPayload,
    TransactionsImportedPayload,
)
from minx_mcp.core.events import Event, emit_event, query_events
```

Do NOT re-export Meals payload classes from `core/__init__.py` — Meals payloads should be imported from `minx_mcp.meals.events` by consumers that need them. This preserves domain ownership.

Update any test files that import payload classes from `minx_mcp.core.events` to import from `minx_mcp.finance.events` instead (or from `minx_mcp.core` which re-exports them).

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: All tests pass (no behavior change, only reorganization)

- [ ] **Step 6: Commit**

```
feat: refactor event registry to per-domain payload declarations

Finance event payload models move from core/events.py to
finance/events.py with a FINANCE_EVENT_PAYLOADS mapping. The shared
PAYLOAD_MODELS dict in core/events.py now composes from domain
declarations instead of owning every payload model directly.
```

---

## Task 2: Meals Schema Migration

Create `010_meals.sql` with all Phase 1 tables. No shopping list tables (Phase 3).

**Files:**
- Create: `minx_mcp/schema/migrations/010_meals.sql`
- Create: `schema/migrations/010_meals.sql` (mirror)
- Modify: `tests/test_db.py`

- [ ] **Step 1: Write failing test — Meals tables exist after migration**

Add to `tests/test_db.py`:

```python
def test_database_bootstrap_creates_meals_tables(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "meals_meal_entries" in names
    assert "meals_pantry_items" in names
    assert "meals_recipes" in names
    assert "meals_recipe_ingredients" in names
    assert "meals_recipe_substitutions" in names
    assert "meals_nutrition_cache" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_database_bootstrap_creates_meals_tables -v`
Expected: FAIL (tables don't exist yet)

- [ ] **Step 3: Create `010_meals.sql`**

```sql
-- Meals domain tables (Slice 3, Phase 1)

CREATE TABLE meals_meal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    meal_kind TEXT NOT NULL DEFAULT 'other',
    summary TEXT,
    food_items_json TEXT NOT NULL DEFAULT '[]',
    protein_grams REAL,
    calories INTEGER,
    carbs_grams REAL,
    fat_grams REAL,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_meals_entries_occurred ON meals_meal_entries(occurred_at);

CREATE TABLE meals_pantry_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    quantity REAL,
    unit TEXT,
    expiration_date TEXT,
    low_stock_threshold REAL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_meals_pantry_normalized ON meals_pantry_items(normalized_name);

CREATE TABLE meals_recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    source_url TEXT,
    image_ref TEXT,
    prep_time_minutes INTEGER,
    cook_time_minutes INTEGER,
    servings INTEGER,
    tags_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    nutrition_summary_json TEXT,
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE meals_recipe_ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES meals_recipes(id) ON DELETE CASCADE,
    display_text TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    quantity REAL,
    unit TEXT,
    is_required INTEGER NOT NULL DEFAULT 1,
    ingredient_group TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE INDEX idx_meals_ingredients_recipe ON meals_recipe_ingredients(recipe_id);
CREATE INDEX idx_meals_ingredients_normalized ON meals_recipe_ingredients(normalized_name);

CREATE TABLE meals_recipe_substitutions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_ingredient_id INTEGER NOT NULL REFERENCES meals_recipe_ingredients(id) ON DELETE CASCADE,
    substitute_normalized_name TEXT NOT NULL,
    display_text TEXT NOT NULL,
    quantity REAL,
    unit TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE INDEX idx_meals_substitutions_ingredient ON meals_recipe_substitutions(recipe_ingredient_id);

CREATE TABLE meals_nutrition_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    calories_per_100g INTEGER,
    protein_per_100g REAL,
    carbs_per_100g REAL,
    fat_per_100g REAL,
    lookup_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(normalized_name, source)
);
```

Copy identical content to `schema/migrations/010_meals.sql`.

- [ ] **Step 4: Update migration count assertions in `tests/test_db.py`**

Update `test_migrations_are_idempotent` and `test_apply_migrations_handles_plain_sqlite_connections` to expect count `10` instead of `9`. Update `test_built_wheel_includes_packaged_migrations` to assert `010_meals.sql`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```
feat: add Meals domain schema (010_meals.sql)

Tables: meals_meal_entries, meals_pantry_items, meals_recipes,
meals_recipe_ingredients, meals_recipe_substitutions,
meals_nutrition_cache. Shopping list tables deferred to Phase 3.
```

---

## Task 3: Meals Domain Models

Define the domain dataclasses that service, read API, and recommendation code will use.

**Files:**
- Create: `minx_mcp/meals/__init__.py`
- Create: `minx_mcp/meals/models.py`

- [ ] **Step 1: Create package init**

`minx_mcp/meals/__init__.py`:

```python
__version__ = "0.1.0"
```

- [ ] **Step 2: Create `meals/models.py` with domain dataclasses**

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MealEntry:
    id: int
    occurred_at: str
    meal_kind: str
    summary: str | None
    food_items: list[dict[str, object]]
    protein_grams: float | None
    calories: int | None
    carbs_grams: float | None
    fat_grams: float | None
    notes: str | None
    source: str


@dataclass(frozen=True)
class PantryItem:
    id: int
    display_name: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    expiration_date: str | None
    low_stock_threshold: float | None
    source: str


@dataclass(frozen=True)
class RecipeIngredient:
    id: int
    recipe_id: int
    display_text: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    is_required: bool
    ingredient_group: str | None
    sort_order: int
    notes: str | None


@dataclass(frozen=True)
class RecipeSubstitution:
    id: int
    recipe_ingredient_id: int
    substitute_normalized_name: str
    display_text: str
    quantity: float | None
    unit: str | None
    priority: int
    notes: str | None


@dataclass(frozen=True)
class Recipe:
    id: int
    vault_path: str
    title: str
    normalized_title: str
    source_url: str | None
    image_ref: str | None
    prep_time_minutes: int | None
    cook_time_minutes: int | None
    servings: int | None
    tags: list[str]
    notes: str | None
    nutrition_summary: dict[str, object] | None
    content_hash: str
    ingredients: list[RecipeIngredient] = field(default_factory=list)
    substitutions: list[RecipeSubstitution] = field(default_factory=list)


@dataclass(frozen=True)
class RecipeRecommendation:
    recipe_id: int
    recipe_title: str
    availability_class: str  # make_now | make_with_substitutions | needs_shopping | excluded
    pantry_coverage_ratio: float
    expiring_ingredient_hits: int
    low_stock_ingredient_hits: int
    missing_required_count: int
    substitution_count: int
    matched_ingredients: list[str]
    substitutions: list[dict[str, str]]
    missing_required_ingredients: list[str]
    reasons: list[str]
    recipe: RecipeMetadata


@dataclass(frozen=True)
class RecipeMetadata:
    vault_path: str
    image_ref: str | None
    tags: list[str]
    source_url: str | None


@dataclass(frozen=True)
class RecommendationResult:
    recommendations: list[RecipeRecommendation]
    included_classes: list[str]
    shopping_lists_generated: list[object] = field(default_factory=list)
```

- [ ] **Step 3: Verify import works**

Run: `.venv/bin/python -c "from minx_mcp.meals.models import MealEntry, PantryItem, Recipe, RecipeRecommendation; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```
feat: add Meals domain models

Dataclasses for MealEntry, PantryItem, Recipe, RecipeIngredient,
RecipeSubstitution, RecipeRecommendation, and RecommendationResult.
```

---

## Task 4: Meals Events — Payload Models and Registration

Register Meals event payloads through the composable registry from Task 1.

**Files:**
- Create: `minx_mcp/meals/events.py`
- Modify: `minx_mcp/core/events.py` (add `MEALS_EVENT_PAYLOADS` to composition)
- Test: `tests/test_meals_events.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_meals_events.py`:

```python
from minx_mcp.core.events import PAYLOAD_MODELS, emit_event
from minx_mcp.db import get_connection


def test_payload_models_includes_meals_events():
    assert "meal.logged" in PAYLOAD_MODELS
    assert "nutrition.day_updated" in PAYLOAD_MODELS


def test_emit_meal_logged_event(db_conn):
    event_id = emit_event(
        db_conn,
        event_type="meal.logged",
        domain="meals",
        occurred_at="2026-04-12T12:30:00Z",
        entity_ref="meal-1",
        source="meals.service",
        payload={
            "meal_id": 1,
            "meal_kind": "lunch",
            "food_count": 3,
            "protein_grams": 45.0,
            "calories": 850,
        },
    )
    assert event_id is not None


def test_emit_meal_logged_rejects_extra_fields(db_conn):
    event_id = emit_event(
        db_conn,
        event_type="meal.logged",
        domain="meals",
        occurred_at="2026-04-12T12:30:00Z",
        entity_ref="meal-1",
        source="meals.service",
        payload={
            "meal_id": 1,
            "meal_kind": "lunch",
            "food_count": 3,
            "bogus_field": "should fail",
        },
    )
    assert event_id is None


def test_emit_nutrition_day_updated(db_conn):
    event_id = emit_event(
        db_conn,
        event_type="nutrition.day_updated",
        domain="meals",
        occurred_at="2026-04-12T23:00:00Z",
        entity_ref="2026-04-12",
        source="meals.service",
        payload={
            "date": "2026-04-12",
            "meal_count": 3,
            "protein_grams": 120.0,
            "calories": 2100,
        },
    )
    assert event_id is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_events.py -v`
Expected: FAIL on `test_payload_models_includes_meals_events`

- [ ] **Step 3: Create `minx_mcp/meals/events.py`**

```python
from __future__ import annotations

from minx_mcp.core.events import EventPayload


class MealLoggedPayload(EventPayload):
    meal_id: int
    meal_kind: str
    food_count: int
    protein_grams: float | None = None
    calories: int | None = None


class NutritionDayUpdatedPayload(EventPayload):
    date: str
    meal_count: int
    protein_grams: float | None = None
    calories: int | None = None


MEALS_EVENT_PAYLOADS: dict[str, type[EventPayload]] = {
    "meal.logged": MealLoggedPayload,
    "nutrition.day_updated": NutritionDayUpdatedPayload,
}
```

- [ ] **Step 4: Compose into `PAYLOAD_MODELS` in `core/events.py`**

Add to the composition in `core/events.py`:

```python
from minx_mcp.meals.events import MEALS_EVENT_PAYLOADS

PAYLOAD_MODELS: dict[str, type[EventPayload]] = {
    **FINANCE_EVENT_PAYLOADS,
    **MEALS_EVENT_PAYLOADS,
}
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_events.py -v`
Expected: All pass

- [ ] **Step 6: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: All pass

- [ ] **Step 7: Commit**

```
feat: register Meals event payloads (meal.logged, nutrition.day_updated)

Meals declares its own MEALS_EVENT_PAYLOADS mapping; core/events.py
composes it alongside FINANCE_EVENT_PAYLOADS.
```

---

## Task 5: Meals Service — Meal Logging and Pantry Management

Build the service layer with connection management, meal logging, and pantry CRUD.

**Files:**
- Create: `minx_mcp/meals/service.py`
- Modify: `tests/helpers.py` (add `MealsSeeder`)
- Modify: `tests/conftest.py` (add `meals_seeder` fixture)
- Test: `tests/test_meals_service.py`

- [ ] **Step 1: Write `MealsSeeder` in `tests/helpers.py`**

Add after `FinanceSeeder`:

```python
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
        import json
        cursor = self._conn.execute(
            """
            INSERT INTO meals_meal_entries (occurred_at, meal_kind, summary, food_items_json,
                protein_grams, calories, source)
            VALUES (?, ?, ?, ?, ?, ?, 'test')
            """,
            (occurred_at, meal_kind, summary, json.dumps(food_items or []),
             protein_grams, calories),
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
            INSERT INTO meals_pantry_items (display_name, normalized_name, quantity, unit,
                expiration_date, low_stock_threshold, source)
            VALUES (?, ?, ?, ?, ?, ?, 'test')
            """,
            (display_name, normalized_name or display_name.lower().strip(),
             quantity, unit, expiration_date, low_stock_threshold),
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
    ) -> int:
        import json
        cursor = self._conn.execute(
            """
            INSERT INTO meals_recipes (vault_path, title, normalized_title, source_url,
                image_ref, tags_json, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (vault_path, title, title.lower().strip(), source_url, image_ref,
             json.dumps(tags or []), content_hash),
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
            INSERT INTO meals_recipe_ingredients (recipe_id, display_text, normalized_name,
                quantity, unit, is_required, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
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
            INSERT INTO meals_recipe_substitutions (recipe_ingredient_id,
                substitute_normalized_name, display_text, priority)
            VALUES (?, ?, ?, ?)
            """,
            (recipe_ingredient_id, substitute_normalized_name, display_text, priority),
        )
        self._conn.commit()
        return cursor.lastrowid or 0
```

Add `meals_seeder` fixture to `tests/conftest.py`:

```python
from tests.helpers import MealsSeeder

@pytest.fixture
def meals_seeder(db_conn):
    return MealsSeeder(db_conn)
```

- [ ] **Step 2: Write failing service tests**

`tests/test_meals_service.py`:

```python
from minx_mcp.meals.service import MealsService


def test_log_meal(db_path):
    svc = MealsService(db_path)
    with svc:
        entry = svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            summary="Grilled chicken salad",
            food_items=[{"name": "chicken breast", "quantity": 200, "unit": "g"}],
            protein_grams=45.0,
            calories=550,
        )
    assert entry.id > 0
    assert entry.meal_kind == "lunch"
    assert entry.protein_grams == 45.0


def test_log_meal_validates_meal_kind(db_path):
    from minx_mcp.contracts import InvalidInputError
    import pytest

    svc = MealsService(db_path)
    with pytest.raises(InvalidInputError):
        with svc:
            svc.log_meal(
                occurred_at="2026-04-12T12:00:00Z",
                meal_kind="invalid_kind",
            )


def test_add_pantry_item(db_path):
    svc = MealsService(db_path)
    with svc:
        item = svc.add_pantry_item(display_name="Chicken Breast", quantity=500, unit="g")
    assert item.id > 0
    assert item.normalized_name == "chicken breast"


def test_update_pantry_item(db_path):
    svc = MealsService(db_path)
    with svc:
        item = svc.add_pantry_item(display_name="Eggs", quantity=12, unit="count")
        updated = svc.update_pantry_item(item.id, quantity=6)
    assert updated.quantity == 6


def test_remove_pantry_item(db_path):
    from minx_mcp.contracts import NotFoundError
    import pytest

    svc = MealsService(db_path)
    with svc:
        item = svc.add_pantry_item(display_name="Milk", quantity=1, unit="L")
        svc.remove_pantry_item(item.id)
        with pytest.raises(NotFoundError):
            svc.get_pantry_item(item.id)


def test_list_pantry_items(db_path):
    svc = MealsService(db_path)
    with svc:
        svc.add_pantry_item(display_name="Eggs", quantity=12, unit="count")
        svc.add_pantry_item(display_name="Milk", quantity=1, unit="L")
        items = svc.list_pantry_items()
    assert len(items) == 2


def test_list_meals_by_date(db_path):
    svc = MealsService(db_path)
    with svc:
        svc.log_meal(occurred_at="2026-04-12T08:00:00Z", meal_kind="breakfast")
        svc.log_meal(occurred_at="2026-04-12T12:00:00Z", meal_kind="lunch")
        svc.log_meal(occurred_at="2026-04-11T12:00:00Z", meal_kind="lunch")
        entries = svc.list_meals(date="2026-04-12")
    assert len(entries) == 2


def test_log_meal_emits_event(db_path):
    from minx_mcp.core.events import query_events
    from minx_mcp.db import get_connection

    svc = MealsService(db_path)
    with svc:
        svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            food_items=[{"name": "rice"}, {"name": "chicken"}],
            protein_grams=40.0,
            calories=700,
        )
    conn = get_connection(db_path)
    events = query_events(conn, domain="meals", event_type="meal.logged")
    assert len(events) == 1
    assert events[0].payload["food_count"] == 2
    assert events[0].payload["protein_grams"] == 40.0
    conn.close()
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_service.py -v`
Expected: FAIL (no MealsService yet)

- [ ] **Step 4: Implement `meals/service.py`**

Follow the Finance service pattern: threading.local() for connection, context manager, `EVENT_SOURCE = "meals.service"`. Implement:
- `log_meal()` — insert into `meals_meal_entries`, emit `meal.logged` event, return `MealEntry`
- `add_pantry_item()` — insert into `meals_pantry_items`, return `PantryItem`
- `update_pantry_item()` — update row, return `PantryItem`
- `remove_pantry_item()` — delete row, raise `NotFoundError` if missing
- `get_pantry_item()` — fetch single item, raise `NotFoundError` if missing
- `list_pantry_items()` — return all pantry items
- `list_meals()` — return meals optionally filtered by date

Valid `meal_kind` values: `breakfast`, `lunch`, `dinner`, `snack`, `other`. Raise `InvalidInputError` for invalid kinds.

Ingredient normalization for pantry: lowercase, strip whitespace. This is the minimal deterministic normalizer; the LLM-fallback path comes in Task 10.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_service.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```
feat: add Meals service with meal logging and pantry management

MealsService follows the Finance service pattern: thread-local DB
connections, context manager, event emission on meal logging.
Supports CRUD for pantry items and date-filtered meal queries.
```

---

## Task 6: Meals Read API and Core NutritionSnapshot

Build the read-only Core-facing interface and integrate NutritionSnapshot into Core's daily snapshot pipeline.

**Files:**
- Create: `minx_mcp/meals/read_api.py`
- Modify: `minx_mcp/core/models.py` (add `MealsReadInterface`, `NutritionSnapshot`)
- Modify: `minx_mcp/core/read_models.py` (add nutrition to `ReadModels` assembly, add event summaries)
- Modify: `minx_mcp/core/snapshot.py` (add nutrition to `DailySnapshot`)
- Test: `tests/test_meals_read_api.py`

- [ ] **Step 1: Write failing tests**

`tests/test_meals_read_api.py`:

```python
from minx_mcp.meals.read_api import MealsReadAPI


def test_get_nutrition_summary_no_meals(db_conn):
    api = MealsReadAPI(db_conn)
    summary = api.get_nutrition_summary("2026-04-12")
    assert summary.date == "2026-04-12"
    assert summary.meal_count == 0
    assert summary.protein_grams is None
    assert summary.calories is None
    assert summary.last_meal_at is None
    assert summary.skipped_meal_signals == []


def test_get_nutrition_summary_with_meals(db_conn, meals_seeder):
    meals_seeder.meal_entry(
        occurred_at="2026-04-12T08:00:00Z",
        meal_kind="breakfast",
        protein_grams=30.0,
        calories=500,
    )
    meals_seeder.meal_entry(
        occurred_at="2026-04-12T13:00:00Z",
        meal_kind="lunch",
        protein_grams=45.0,
        calories=750,
    )
    api = MealsReadAPI(db_conn)
    summary = api.get_nutrition_summary("2026-04-12")
    assert summary.meal_count == 2
    assert summary.protein_grams == 75.0
    assert summary.calories == 1250
    assert summary.last_meal_at == "2026-04-12T13:00:00Z"


def test_get_nutrition_summary_skipped_meal_signals(db_conn, meals_seeder):
    # Only dinner logged — breakfast and lunch missing
    meals_seeder.meal_entry(
        occurred_at="2026-04-12T19:00:00Z",
        meal_kind="dinner",
        protein_grams=50.0,
        calories=800,
    )
    api = MealsReadAPI(db_conn)
    summary = api.get_nutrition_summary("2026-04-12")
    assert summary.meal_count == 1
    assert "no breakfast logged" in summary.skipped_meal_signals
    assert "no lunch logged" in summary.skipped_meal_signals


def test_summarize_meal_logged_event(db_conn, meals_seeder):
    from minx_mcp.core.events import emit_event, query_events
    from minx_mcp.core.read_models import _summarize_event

    emit_event(
        db_conn,
        event_type="meal.logged",
        domain="meals",
        occurred_at="2026-04-12T12:00:00Z",
        entity_ref="meal-1",
        source="test",
        payload={"meal_id": 1, "meal_kind": "lunch", "food_count": 2, "calories": 700},
    )
    events = query_events(db_conn, domain="meals")
    summary = _summarize_event(events[0])
    assert "lunch" in summary
    assert "700" in summary


def test_summarize_nutrition_day_updated_event(db_conn):
    from minx_mcp.core.events import emit_event, query_events
    from minx_mcp.core.read_models import _summarize_event

    emit_event(
        db_conn,
        event_type="nutrition.day_updated",
        domain="meals",
        occurred_at="2026-04-12T23:00:00Z",
        entity_ref="2026-04-12",
        source="test",
        payload={"date": "2026-04-12", "meal_count": 3, "protein_grams": 120.0, "calories": 2100},
    )
    events = query_events(db_conn, domain="meals")
    summary = _summarize_event(events[0])
    assert "3 meals" in summary
    assert "120" in summary
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_read_api.py -v`
Expected: FAIL

- [ ] **Step 3: Add `MealsReadInterface` Protocol and `NutritionSnapshot` to `core/models.py`**

```python
class MealsReadInterface(Protocol):
    def get_nutrition_summary(self, date: str) -> Any: ...
    def get_pantry_items(self) -> list[Any]: ...
```

```python
@dataclass(frozen=True)
class NutritionSnapshot:
    date: str
    meal_count: int
    protein_grams: float | None
    calories: int | None
    last_meal_at: str | None
    skipped_meal_signals: list[str]
```

Add `nutrition: NutritionSnapshot | None = None` to `ReadModels` and `DailySnapshot`.

- [ ] **Step 4: Implement `meals/read_api.py`**

```python
from __future__ import annotations

from sqlite3 import Connection

from minx_mcp.core.models import NutritionSnapshot


class MealsReadAPI:
    def __init__(self, db: Connection) -> None:
        self._db = db

    def get_nutrition_summary(self, date: str) -> NutritionSnapshot:
        # Query meals_meal_entries for the given date
        # Sum protein_grams, calories
        # Detect skipped meals (check which of breakfast/lunch/dinner are missing)
        # Return NutritionSnapshot
        ...

    def get_pantry_items(self):
        # Query meals_pantry_items, return list of PantryItem
        ...
```

For `get_nutrition_summary`: query `meals_meal_entries` where `occurred_at` falls on the given date. Use the same `_next_day` exclusive-end pattern as Finance. Sum nutrition fields (ignoring None values). Generate `skipped_meal_signals` by checking which of `breakfast`, `lunch`, `dinner` are absent from logged `meal_kind` values.

- [ ] **Step 5: Update `core/read_models.py`**

In `build_read_models()`, optionally accept a `meals_api: MealsReadInterface | None` parameter. If provided, call `meals_api.get_nutrition_summary(review_date)` and include in the returned `ReadModels`. If not provided, try to auto-construct `MealsReadAPI(conn)` following the existing `finance_api or FinanceReadAPI(conn)` pattern.

Update `core/models.py` `SnapshotContext` to add `meals_api`:

```python
@dataclass(frozen=True)
class SnapshotContext:
    db_path: Path
    finance_api: FinanceReadInterface | None = None
    meals_api: MealsReadInterface | None = None
```

Update `core/snapshot.py` `_build_snapshot_models()` to pass `meals_api` from the context through to `build_read_models()`. Also update the `DailySnapshot` constructor call in `build_daily_snapshot()` to include `nutrition=read_models.nutrition`.

Add event summary cases to `_summarize_event()`:

```python
if event.event_type == "meal.logged":
    kind = payload.get("meal_kind", "meal")
    cal = payload.get("calories")
    cal_str = f" ({cal} cal)" if cal else ""
    return f"Logged {kind}{cal_str}"
if event.event_type == "nutrition.day_updated":
    return (
        f"Nutrition update: {payload['meal_count']} meals, "
        f"{payload.get('protein_grams', '?')}g protein"
    )
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_read_api.py -v`
Expected: All pass

- [ ] **Step 7: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: All pass

- [ ] **Step 8: Commit**

```
feat: add Meals read API and NutritionSnapshot in Core

MealsReadAPI provides get_nutrition_summary() for Core snapshot
assembly. NutritionSnapshot is a discrete field on ReadModels and
DailySnapshot. Event summaries handle meal.logged and
nutrition.day_updated.
```

---

## Task 7: Nutrition Detectors

Add deterministic detectors for low protein and skipped meals.

**Files:**
- Modify: `minx_mcp/core/detectors.py`
- Test: `tests/test_nutrition_detectors.py`

- [ ] **Step 1: Write failing tests**

`tests/test_nutrition_detectors.py`:

```python
from minx_mcp.core.detectors import DETECTORS
from minx_mcp.core.models import (
    DailyTimeline, InsightCandidate, NutritionSnapshot, OpenLoopsSnapshot,
    ReadModels, SpendingSnapshot,
)


def _build_read_models(*, nutrition: NutritionSnapshot | None = None) -> ReadModels:
    return ReadModels(
        timeline=DailyTimeline(date="2026-04-12", entries=[]),
        spending=SpendingSnapshot(
            date="2026-04-12", total_spent_cents=0, by_category={},
            top_merchants=[], vs_prior_week_pct=None,
            uncategorized_count=0, uncategorized_total_cents=0,
        ),
        open_loops=OpenLoopsSnapshot(date="2026-04-12", loops=[]),
        goal_progress=[],
        nutrition=nutrition,
    )


def test_detector_registry_includes_nutrition_detectors():
    keys = [d.key for d in DETECTORS]
    assert "nutrition.low_protein" in keys
    assert "nutrition.skipped_meals" in keys


def test_low_protein_detector_fires_when_protein_under_threshold():
    from minx_mcp.core.detectors import detect_low_protein

    nutrition = NutritionSnapshot(
        date="2026-04-12", meal_count=3, protein_grams=40.0,
        calories=2000, last_meal_at="2026-04-12T19:00:00Z",
        skipped_meal_signals=[],
    )
    insights = detect_low_protein(_build_read_models(nutrition=nutrition))
    assert len(insights) == 1
    assert insights[0].severity == "info"
    assert "40" in insights[0].summary


def test_low_protein_detector_silent_when_no_nutrition():
    from minx_mcp.core.detectors import detect_low_protein

    insights = detect_low_protein(_build_read_models(nutrition=None))
    assert insights == []


def test_low_protein_detector_silent_when_above_threshold():
    from minx_mcp.core.detectors import detect_low_protein

    nutrition = NutritionSnapshot(
        date="2026-04-12", meal_count=3, protein_grams=120.0,
        calories=2200, last_meal_at="2026-04-12T19:00:00Z",
        skipped_meal_signals=[],
    )
    insights = detect_low_protein(_build_read_models(nutrition=nutrition))
    assert insights == []


def test_skipped_meals_detector_fires_on_missing_meals():
    from minx_mcp.core.detectors import detect_skipped_meals

    nutrition = NutritionSnapshot(
        date="2026-04-12", meal_count=1, protein_grams=50.0,
        calories=800, last_meal_at="2026-04-12T19:00:00Z",
        skipped_meal_signals=["no breakfast logged", "no lunch logged"],
    )
    insights = detect_skipped_meals(_build_read_models(nutrition=nutrition))
    assert len(insights) == 1
    assert "breakfast" in insights[0].summary
    assert "lunch" in insights[0].summary


def test_skipped_meals_detector_silent_when_all_meals_present():
    from minx_mcp.core.detectors import detect_skipped_meals

    nutrition = NutritionSnapshot(
        date="2026-04-12", meal_count=3, protein_grams=120.0,
        calories=2200, last_meal_at="2026-04-12T19:00:00Z",
        skipped_meal_signals=[],
    )
    insights = detect_skipped_meals(_build_read_models(nutrition=nutrition))
    assert insights == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_nutrition_detectors.py -v`
Expected: FAIL

- [ ] **Step 3: Implement detectors**

Add to `minx_mcp/core/detectors.py`:

```python
LOW_PROTEIN_THRESHOLD_GRAMS = 50.0


def detect_low_protein(read_models: ReadModels) -> list[InsightCandidate]:
    nutrition = read_models.nutrition
    if nutrition is None or nutrition.protein_grams is None:
        return []
    if nutrition.protein_grams >= LOW_PROTEIN_THRESHOLD_GRAMS:
        return []
    return [
        InsightCandidate(
            insight_type="nutrition.low_protein",
            dedupe_key=f"{nutrition.date}:low_protein",
            summary=f"Protein intake is {nutrition.protein_grams:.0f}g today, below {LOW_PROTEIN_THRESHOLD_GRAMS:.0f}g target.",
            supporting_signals=[f"{nutrition.meal_count} meals logged"],
            confidence=0.9,
            severity="info",
            actionability="suggestion",
            source="detector",
        )
    ]


def detect_skipped_meals(read_models: ReadModels) -> list[InsightCandidate]:
    nutrition = read_models.nutrition
    if nutrition is None or not nutrition.skipped_meal_signals:
        return []
    skipped = ", ".join(
        s.replace("no ", "").replace(" logged", "") for s in nutrition.skipped_meal_signals
    )
    return [
        InsightCandidate(
            insight_type="nutrition.skipped_meals",
            dedupe_key=f"{nutrition.date}:skipped_meals",
            summary=f"Missing meals today: {skipped}.",
            supporting_signals=nutrition.skipped_meal_signals,
            confidence=0.85,
            severity="info",
            actionability="suggestion",
            source="detector",
        )
    ]
```

Register in `DETECTORS`:

```python
Detector(key="nutrition.low_protein", fn=detect_low_protein, tags=frozenset({"nutrition"})),
Detector(key="nutrition.skipped_meals", fn=detect_skipped_meals, tags=frozenset({"nutrition"})),
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_nutrition_detectors.py -v`
Expected: All pass

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: All pass

- [ ] **Step 6: Commit**

```
feat: add nutrition detectors (low protein, skipped meals)

Deterministic detectors check NutritionSnapshot for protein below
50g threshold and missing breakfast/lunch/dinner. Registered in
DETECTORS with nutrition tags.
```

---

## Task 8: Recipe Parsing from Obsidian

Parse recipe markdown notes into structured data for indexing.

**Files:**
- Create: `minx_mcp/meals/recipes.py`
- Test: `tests/test_meals_recipes.py`

- [ ] **Step 1: Write failing tests**

`tests/test_meals_recipes.py`:

```python
from pathlib import Path
from minx_mcp.meals.recipes import parse_recipe_note


SAMPLE_RECIPE = """\
---
title: Chickpea Pasta
tags: [dinner, vegetarian]
prep_time: 10
cook_time: 20
servings: 4
source: https://example.com/chickpea-pasta
image: Assets/chickpea-pasta.jpg
---

# Chickpea Pasta

## Ingredients

- 400g pasta
- 1 can chickpeas, drained
- 2 cups fresh spinach
- 3 cloves garlic, minced
- 2 tbsp olive oil
- Salt and pepper to taste (optional)

## Substitutions

- chickpeas: white beans, lentils
- spinach: kale

## Instructions

1. Cook pasta according to package directions.
2. Sauté garlic in olive oil.
3. Add chickpeas and spinach.
4. Toss with pasta.

## Notes

Great for meal prep.
"""


def test_parse_recipe_title(tmp_path):
    note = tmp_path / "Chickpea Pasta.md"
    note.write_text(SAMPLE_RECIPE)
    result = parse_recipe_note(note)
    assert result.title == "Chickpea Pasta"


def test_parse_recipe_frontmatter(tmp_path):
    note = tmp_path / "Chickpea Pasta.md"
    note.write_text(SAMPLE_RECIPE)
    result = parse_recipe_note(note)
    assert result.tags == ["dinner", "vegetarian"]
    assert result.prep_time_minutes == 10
    assert result.cook_time_minutes == 20
    assert result.servings == 4
    assert result.source_url == "https://example.com/chickpea-pasta"
    assert result.image_ref == "Assets/chickpea-pasta.jpg"


def test_parse_recipe_ingredients(tmp_path):
    note = tmp_path / "Chickpea Pasta.md"
    note.write_text(SAMPLE_RECIPE)
    result = parse_recipe_note(note)
    assert len(result.ingredients) == 6
    # First ingredient
    assert result.ingredients[0].display_text == "400g pasta"
    assert result.ingredients[0].normalized_name == "pasta"
    assert result.ingredients[0].is_required is True
    # Optional ingredient
    optional = [i for i in result.ingredients if not i.is_required]
    assert len(optional) == 1
    assert optional[0].normalized_name == "salt and pepper"


def test_parse_recipe_substitutions(tmp_path):
    note = tmp_path / "Chickpea Pasta.md"
    note.write_text(SAMPLE_RECIPE)
    result = parse_recipe_note(note)
    assert len(result.substitutions) == 3  # white beans, lentils, kale
    chickpea_subs = [s for s in result.substitutions if s.original_name == "chickpeas"]
    assert len(chickpea_subs) == 2
    assert {s.substitute_name for s in chickpea_subs} == {"white beans", "lentils"}


def test_parse_recipe_content_hash_changes_on_edit(tmp_path):
    note = tmp_path / "Test.md"
    note.write_text("---\ntitle: Test\n---\n## Ingredients\n- 1 egg\n")
    result1 = parse_recipe_note(note)
    note.write_text("---\ntitle: Test\n---\n## Ingredients\n- 2 eggs\n")
    result2 = parse_recipe_note(note)
    assert result1.content_hash != result2.content_hash


def test_parse_recipe_missing_sections_gracefully(tmp_path):
    note = tmp_path / "Minimal.md"
    note.write_text("---\ntitle: Minimal\n---\n## Ingredients\n- 1 egg\n")
    result = parse_recipe_note(note)
    assert result.title == "Minimal"
    assert len(result.ingredients) == 1
    assert result.substitutions == []
    assert result.tags == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_recipes.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `meals/recipes.py`**

Key responsibilities:
- Parse YAML frontmatter for metadata (title, tags, prep_time, cook_time, servings, source, image)
- Parse `## Ingredients` section: each `- ` line is an ingredient. Lines ending with `(optional)` set `is_required=False`
- Normalize ingredient names: strip quantities/units from display text to extract the ingredient name, then lowercase/strip
- Parse `## Substitutions` section: each `- original: sub1, sub2` line
- Compute content hash (SHA-256 of file content)
- Return a `ParsedRecipe` dataclass with structured data ready for DB insertion

Define intermediary dataclasses in the same file:

```python
@dataclass(frozen=True)
class ParsedIngredient:
    display_text: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    is_required: bool
    sort_order: int

@dataclass(frozen=True)
class ParsedSubstitution:
    original_name: str
    substitute_name: str
    display_text: str
    priority: int

@dataclass(frozen=True)
class ParsedRecipe:
    title: str
    tags: list[str]
    prep_time_minutes: int | None
    cook_time_minutes: int | None
    servings: int | None
    source_url: str | None
    image_ref: str | None
    ingredients: list[ParsedIngredient]
    substitutions: list[ParsedSubstitution]
    content_hash: str
    notes: str | None
```

For ingredient parsing, use a simple regex approach: strip leading quantity+unit patterns (digits, fractions, common units like g, kg, ml, cup, tbsp, etc.), remainder is the ingredient name. This is the deterministic path; LLM fallback is deferred.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_recipes.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```
feat: add Obsidian recipe markdown parser

Parses YAML frontmatter, ingredients (with optional detection),
substitutions, and computes content hash. Deterministic ingredient
name extraction via regex; LLM fallback deferred.
```

---

## Task 9: Recipe Indexing in Service

Wire recipe parsing into the service layer so Obsidian recipe notes can be indexed into the DB.

**Files:**
- Modify: `minx_mcp/meals/service.py`
- Test: `tests/test_meals_service.py` (add recipe indexing tests)

- [ ] **Step 1: Write failing tests**

Add to `tests/test_meals_service.py`:

```python
def test_index_recipe_from_vault(db_path, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    recipes_dir = vault / "Recipes"
    recipes_dir.mkdir()
    (recipes_dir / "Test Pasta.md").write_text(
        "---\ntitle: Test Pasta\ntags: [dinner]\n---\n"
        "## Ingredients\n- 400g pasta\n- 1 can tomatoes\n"
    )

    svc = MealsService(db_path, vault_root=vault)
    with svc:
        recipe = svc.index_recipe("Recipes/Test Pasta.md")
    assert recipe.title == "Test Pasta"
    assert recipe.vault_path == "Recipes/Test Pasta.md"
    assert len(recipe.ingredients) == 2


def test_index_recipe_updates_on_content_change(db_path, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Recipes" / "Soup.md"
    note.parent.mkdir()
    note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n")

    svc = MealsService(db_path, vault_root=vault)
    with svc:
        r1 = svc.index_recipe("Recipes/Soup.md")
        note.write_text("---\ntitle: Soup\n---\n## Ingredients\n- 1 onion\n- 2 carrots\n")
        r2 = svc.index_recipe("Recipes/Soup.md")
    assert r2.id == r1.id  # same row, updated
    assert len(r2.ingredients) == 2


def test_index_recipe_skips_unchanged(db_path, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "Recipes" / "Rice.md"
    note.parent.mkdir()
    note.write_text("---\ntitle: Rice\n---\n## Ingredients\n- 1 cup rice\n")

    svc = MealsService(db_path, vault_root=vault)
    with svc:
        r1 = svc.index_recipe("Recipes/Rice.md")
        r2 = svc.index_recipe("Recipes/Rice.md")
    assert r1.content_hash == r2.content_hash


def test_scan_vault_recipes(db_path, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    recipes_dir = vault / "Recipes"
    recipes_dir.mkdir()
    (recipes_dir / "A.md").write_text("---\ntitle: A\n---\n## Ingredients\n- 1 egg\n")
    (recipes_dir / "B.md").write_text("---\ntitle: B\n---\n## Ingredients\n- 1 apple\n")
    (recipes_dir / "not-a-recipe.txt").write_text("ignore me")

    svc = MealsService(db_path, vault_root=vault)
    with svc:
        indexed = svc.scan_vault_recipes(directory="Recipes")
    assert len(indexed) == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_service.py::test_index_recipe_from_vault -v`
Expected: FAIL

- [ ] **Step 3: Implement recipe indexing in `meals/service.py`**

Add to `MealsService`:
- `index_recipe(relative_path: str) -> Recipe` — parse the note, upsert into `meals_recipes` + `meals_recipe_ingredients` + `meals_recipe_substitutions`, skip if content hash unchanged
- `scan_vault_recipes(directory: str = "Recipes") -> list[Recipe]` — glob `*.md` in the directory, index each
- `get_recipe(recipe_id: int) -> Recipe` — fetch recipe with ingredients and substitutions
- `list_recipes() -> list[Recipe]` — fetch all indexed recipes (without ingredients for listing)

The `MealsService.__init__` gains an optional `vault_root: Path | None = None` parameter.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_service.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```
feat: add recipe indexing from Obsidian vault

MealsService.index_recipe() parses a vault note, upserts into
meals_recipes with ingredients and substitutions. Content hash
prevents re-indexing unchanged notes. scan_vault_recipes() indexes
all .md files in a vault directory.
```

---

## Task 10: Pantry Matching and Normalization

Build the deterministic pantry-matching logic that recipe recommendation depends on.

**Files:**
- Create: `minx_mcp/meals/pantry.py`
- Test: `tests/test_meals_pantry.py`

- [ ] **Step 1: Write failing tests**

`tests/test_meals_pantry.py`:

```python
from minx_mcp.meals.pantry import normalize_ingredient, match_pantry


def test_normalize_ingredient_basic():
    assert normalize_ingredient("Chicken Breast") == "chicken breast"
    assert normalize_ingredient("  Fresh Spinach  ") == "fresh spinach"
    assert normalize_ingredient("GARLIC") == "garlic"


def test_normalize_ingredient_strips_plurals():
    assert normalize_ingredient("tomatoes") == "tomato"
    assert normalize_ingredient("chickpeas") == "chickpea"
    assert normalize_ingredient("eggs") == "egg"


def test_match_pantry_exact_match(db_conn, meals_seeder):
    meals_seeder.pantry_item(display_name="Pasta", quantity=500, unit="g")
    from minx_mcp.meals.pantry import match_pantry

    matches = match_pantry(db_conn, ["pasta"])
    assert "pasta" in matches
    assert matches["pasta"].display_name == "Pasta"


def test_match_pantry_normalized_match(db_conn, meals_seeder):
    meals_seeder.pantry_item(display_name="Chicken Breast", quantity=500, unit="g")

    matches = match_pantry(db_conn, ["chicken breast"])
    assert "chicken breast" in matches


def test_match_pantry_missing_ingredient(db_conn, meals_seeder):
    meals_seeder.pantry_item(display_name="Pasta", quantity=500, unit="g")

    matches = match_pantry(db_conn, ["pasta", "salmon"])
    assert "pasta" in matches
    assert "salmon" not in matches


def test_match_pantry_expiring_items(db_conn, meals_seeder):
    meals_seeder.pantry_item(
        display_name="Spinach",
        quantity=200,
        unit="g",
        expiration_date="2026-04-14",
    )
    from minx_mcp.meals.pantry import get_expiring_items

    expiring = get_expiring_items(db_conn, as_of="2026-04-12", days_ahead=3)
    assert len(expiring) == 1
    assert expiring[0].display_name == "Spinach"


def test_match_pantry_low_stock_items(db_conn, meals_seeder):
    meals_seeder.pantry_item(
        display_name="Eggs",
        quantity=2,
        unit="count",
        low_stock_threshold=6,
    )
    from minx_mcp.meals.pantry import get_low_stock_items

    low = get_low_stock_items(db_conn)
    assert len(low) == 1
    assert low[0].display_name == "Eggs"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_pantry.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `meals/pantry.py`**

```python
from __future__ import annotations

import re
from sqlite3 import Connection

from minx_mcp.meals.models import PantryItem

# Plural stripping: simple suffix rules (not exhaustive, LLM fallback covers the rest)
_PLURAL_SUFFIXES = [("ies", "y"), ("ves", "f"), ("es", ""), ("s", "")]


def normalize_ingredient(name: str) -> str:
    result = name.lower().strip()
    for suffix, replacement in _PLURAL_SUFFIXES:
        if result.endswith(suffix) and len(result) > len(suffix) + 1:
            candidate = result[: -len(suffix)] + replacement
            # Avoid over-stripping (e.g., "rice" -> "ric")
            if len(candidate) >= 3:
                result = candidate
                break
    return result


def match_pantry(
    conn: Connection,
    ingredient_names: list[str],
) -> dict[str, PantryItem]:
    """Match ingredient names against pantry. Returns {normalized_name: PantryItem}."""
    ...


def get_expiring_items(
    conn: Connection,
    as_of: str,
    days_ahead: int = 3,
) -> list[PantryItem]:
    """Return pantry items expiring within days_ahead of as_of date."""
    ...


def get_low_stock_items(conn: Connection) -> list[PantryItem]:
    """Return pantry items where quantity < low_stock_threshold."""
    ...
```

`match_pantry`: query `meals_pantry_items` with `WHERE normalized_name IN (?)` for the list of names. Return a dict keyed by normalized_name.

`get_expiring_items`: query where `expiration_date IS NOT NULL AND expiration_date <= date(as_of, '+N days')`.

`get_low_stock_items`: query where `low_stock_threshold IS NOT NULL AND quantity IS NOT NULL AND quantity < low_stock_threshold`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_pantry.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```
feat: add pantry matching and ingredient normalization

Deterministic ingredient normalization (lowercase, strip, simple
plural stripping). Pantry matching by normalized name. Expiring-item
and low-stock queries for recommendation ranking signals.
```

---

## Task 11: Availability Classification and Ranking

The core recommendation algorithm: classify recipes into availability classes and rank them deterministically.

**Files:**
- Create: `minx_mcp/meals/recommendations.py`
- Test: `tests/test_meals_recommendations.py`

- [ ] **Step 1: Write failing tests — availability classification**

`tests/test_meals_recommendations.py`:

```python
from minx_mcp.meals.recommendations import classify_recipe, rank_recommendations


def test_classify_make_now():
    result = classify_recipe(
        required_names=["pasta", "chickpea", "spinach"],
        optional_names=["parmesan"],
        pantry_names={"pasta", "chickpea", "spinach", "parmesan"},
        substitution_map={},
    )
    assert result.availability_class == "make_now"
    assert result.missing_required_count == 0
    assert result.pantry_coverage_ratio == 1.0


def test_classify_make_with_substitutions():
    result = classify_recipe(
        required_names=["pasta", "chickpea", "spinach"],
        optional_names=[],
        pantry_names={"pasta", "white bean"},
        substitution_map={"chickpea": ["white bean"], "spinach": ["kale"]},
    )
    # chickpea covered by white bean sub, spinach has kale sub but kale not in pantry
    assert result.availability_class == "needs_shopping"


def test_classify_make_with_substitutions_all_covered():
    result = classify_recipe(
        required_names=["pasta", "chickpea", "spinach"],
        optional_names=[],
        pantry_names={"pasta", "white bean", "kale"},
        substitution_map={"chickpea": ["white bean"], "spinach": ["kale"]},
    )
    assert result.availability_class == "make_with_substitutions"
    assert result.substitution_count == 2


def test_classify_needs_shopping():
    result = classify_recipe(
        required_names=["pasta", "chickpea", "spinach"],
        optional_names=[],
        pantry_names={"pasta"},
        substitution_map={},
    )
    assert result.availability_class == "needs_shopping"
    assert result.missing_required_count == 2


def test_classify_excluded_no_ingredients():
    result = classify_recipe(
        required_names=[],
        optional_names=[],
        pantry_names=set(),
        substitution_map={},
    )
    assert result.availability_class == "excluded"


def test_optional_ingredients_dont_affect_classification():
    result = classify_recipe(
        required_names=["pasta"],
        optional_names=["parmesan", "chili flakes"],
        pantry_names={"pasta"},
        substitution_map={},
    )
    assert result.availability_class == "make_now"
    assert result.pantry_coverage_ratio == 1.0


def test_rank_by_availability_class():
    recs = [
        _rec("Soup", "needs_shopping"),
        _rec("Salad", "make_now"),
        _rec("Stew", "make_with_substitutions"),
    ]
    ranked = rank_recommendations(recs)
    assert [r.recipe_title for r in ranked] == ["Salad", "Stew", "Soup"]


def test_rank_within_class_by_expiring_hits():
    recs = [
        _rec("A", "make_now", expiring_ingredient_hits=0),
        _rec("B", "make_now", expiring_ingredient_hits=2),
    ]
    ranked = rank_recommendations(recs)
    assert ranked[0].recipe_title == "B"


def test_rank_tiebreak_by_title():
    recs = [
        _rec("Zebra Pasta", "make_now"),
        _rec("Apple Salad", "make_now"),
    ]
    ranked = rank_recommendations(recs)
    assert ranked[0].recipe_title == "Apple Salad"


def _rec(
    title: str,
    cls: str,
    *,
    expiring_ingredient_hits: int = 0,
    low_stock_ingredient_hits: int = 0,
    pantry_coverage_ratio: float = 1.0,
    missing_required_count: int = 0,
    substitution_count: int = 0,
):
    from minx_mcp.meals.models import RecipeMetadata, RecipeRecommendation

    return RecipeRecommendation(
        recipe_id=0,
        recipe_title=title,
        availability_class=cls,
        pantry_coverage_ratio=pantry_coverage_ratio,
        expiring_ingredient_hits=expiring_ingredient_hits,
        low_stock_ingredient_hits=low_stock_ingredient_hits,
        missing_required_count=missing_required_count,
        substitution_count=substitution_count,
        matched_ingredients=[],
        substitutions=[],
        missing_required_ingredients=[],
        reasons=[],
        recipe=RecipeMetadata(vault_path="", image_ref=None, tags=[], source_url=None),
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_recommendations.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `meals/recommendations.py`**

Two key functions:

**`classify_recipe()`** — takes required ingredient names, optional names, pantry name set, and substitution map. Returns a classification result with:
- `availability_class`: determined by whether all required ingredients are covered by pantry (make_now), by pantry + substitutions (make_with_substitutions), or not (needs_shopping). If no required ingredients exist, return `excluded`.
- `pantry_coverage_ratio`: count of required ingredients covered / total required
- `missing_required_count`, `substitution_count`, `matched_ingredients`, `missing_required_ingredients`

For each required ingredient:
1. Check if it's in pantry names (direct match)
2. If not, check if any substitution for it is in pantry names
3. If neither, it's missing

**`rank_recommendations()`** — sort by the spec's exact ranking key:
1. availability class priority: `make_now` < `make_with_substitutions` < `needs_shopping` < `excluded`
2. `expiring_ingredient_hits` descending
3. `low_stock_ingredient_hits` descending
4. `pantry_coverage_ratio` descending
5. `missing_required_count` ascending
6. `substitution_count` ascending
7. `recipe_title` ascending

```python
_CLASS_ORDER = {"make_now": 0, "make_with_substitutions": 1, "needs_shopping": 2, "excluded": 3}


def rank_recommendations(recs: list[RecipeRecommendation]) -> list[RecipeRecommendation]:
    return sorted(recs, key=lambda r: (
        _CLASS_ORDER.get(r.availability_class, 99),
        -r.expiring_ingredient_hits,
        -r.low_stock_ingredient_hits,
        -r.pantry_coverage_ratio,
        r.missing_required_count,
        r.substitution_count,
        r.recipe_title,
    ))
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_recommendations.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```
feat: add deterministic recipe classification and ranking

classify_recipe() assigns availability classes (make_now,
make_with_substitutions, needs_shopping, excluded) based on pantry
coverage and substitutions. rank_recommendations() applies the
spec's exact sort key with expiring/low-stock ingredient priority.
```

---

## Task 12: End-to-End Recommendation Pipeline

Wire classification and ranking into a `recommend_recipes()` function that takes a DB connection, queries all recipes and pantry, classifies, ranks, and returns the structured recommendation result.

**Files:**
- Modify: `minx_mcp/meals/recommendations.py`
- Test: `tests/test_meals_recommendations.py` (add integration tests)

- [ ] **Step 1: Write failing integration tests**

Add to `tests/test_meals_recommendations.py`:

```python
from minx_mcp.meals.recommendations import recommend_recipes


def test_recommend_recipes_e2e(db_conn, meals_seeder):
    # Recipe 1: fully covered by pantry
    r1 = meals_seeder.recipe(vault_path="Recipes/Pasta.md", title="Simple Pasta")
    meals_seeder.recipe_ingredient(recipe_id=r1, display_text="pasta", normalized_name="pasta")
    meals_seeder.recipe_ingredient(recipe_id=r1, display_text="olive oil", normalized_name="olive oil")

    # Recipe 2: needs shopping
    r2 = meals_seeder.recipe(vault_path="Recipes/Salmon.md", title="Grilled Salmon")
    meals_seeder.recipe_ingredient(recipe_id=r2, display_text="salmon fillet", normalized_name="salmon")
    meals_seeder.recipe_ingredient(recipe_id=r2, display_text="lemon", normalized_name="lemon")

    # Pantry: only has pasta and olive oil
    meals_seeder.pantry_item(display_name="Pasta", quantity=500, unit="g")
    meals_seeder.pantry_item(display_name="Olive Oil", quantity=1, unit="bottle")

    result = recommend_recipes(db_conn)
    assert len(result.recommendations) >= 2
    assert result.recommendations[0].recipe_title == "Simple Pasta"
    assert result.recommendations[0].availability_class == "make_now"
    assert result.recommendations[1].availability_class == "needs_shopping"
    assert result.shopping_lists_generated == []


def test_recommend_recipes_expiring_item_ranks_higher(db_conn, meals_seeder):
    r1 = meals_seeder.recipe(vault_path="Recipes/A.md", title="Recipe A")
    meals_seeder.recipe_ingredient(recipe_id=r1, display_text="rice", normalized_name="rice")

    r2 = meals_seeder.recipe(vault_path="Recipes/B.md", title="Recipe B")
    meals_seeder.recipe_ingredient(recipe_id=r2, display_text="spinach", normalized_name="spinach")

    meals_seeder.pantry_item(display_name="Rice", quantity=500, unit="g")
    meals_seeder.pantry_item(
        display_name="Spinach", quantity=200, unit="g",
        expiration_date="2026-04-14",
    )

    result = recommend_recipes(db_conn, as_of="2026-04-12")
    # Both are make_now, but B uses expiring spinach
    assert result.recommendations[0].recipe_title == "Recipe B"
    assert result.recommendations[0].expiring_ingredient_hits == 1


def test_recommend_recipes_substitution_class(db_conn, meals_seeder):
    r1 = meals_seeder.recipe(vault_path="Recipes/Stew.md", title="Bean Stew")
    ing_id = meals_seeder.recipe_ingredient(
        recipe_id=r1, display_text="kidney beans", normalized_name="kidney bean",
    )
    meals_seeder.recipe_ingredient(
        recipe_id=r1, display_text="onion", normalized_name="onion",
    )
    meals_seeder.substitution(
        recipe_ingredient_id=ing_id,
        substitute_normalized_name="black bean",
        display_text="black beans",
    )

    meals_seeder.pantry_item(display_name="Black Beans", normalized_name="black bean")
    meals_seeder.pantry_item(display_name="Onion", normalized_name="onion")

    result = recommend_recipes(db_conn)
    assert result.recommendations[0].availability_class == "make_with_substitutions"
    assert result.recommendations[0].substitution_count == 1


def test_recommend_recipes_default_excludes_shopping_lists(db_conn, meals_seeder):
    r1 = meals_seeder.recipe(vault_path="Recipes/X.md", title="X")
    meals_seeder.recipe_ingredient(recipe_id=r1, display_text="rare ingredient", normalized_name="truffle")

    result = recommend_recipes(db_conn)
    assert result.shopping_lists_generated == []


def test_recommend_recipes_included_classes_default(db_conn, meals_seeder):
    r1 = meals_seeder.recipe(vault_path="Recipes/Y.md", title="Y")
    meals_seeder.recipe_ingredient(recipe_id=r1, display_text="egg", normalized_name="egg")
    meals_seeder.pantry_item(display_name="Eggs", normalized_name="egg")

    result = recommend_recipes(db_conn)
    assert "make_now" in result.included_classes
    assert "make_with_substitutions" in result.included_classes
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_recommendations.py::test_recommend_recipes_e2e -v`
Expected: FAIL

- [ ] **Step 3: Implement `recommend_recipes()` in `meals/recommendations.py`**

```python
def recommend_recipes(
    conn: Connection,
    *,
    as_of: str | None = None,
    include_needs_shopping: bool = False,
) -> RecommendationResult:
    ...
```

Steps:
1. Load all recipes with their ingredients and substitutions from DB
2. Load all pantry items, build a set of normalized names
3. Load expiring items and low-stock items (using `as_of` or today)
4. For each recipe, call `classify_recipe()` with the pantry set and substitution map
5. Compute `expiring_ingredient_hits` and `low_stock_ingredient_hits` per recipe
6. Build `RecipeRecommendation` objects
7. Rank with `rank_recommendations()`
8. Filter: default returns `make_now` + `make_with_substitutions`; if `include_needs_shopping=True`, also include `needs_shopping`
9. Return `RecommendationResult` with `shopping_lists_generated=[]`

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_recommendations.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```
feat: add end-to-end recipe recommendation pipeline

recommend_recipes() loads all recipes and pantry state, classifies
each recipe by availability, computes expiring/low-stock signals,
ranks deterministically, and returns structured results. Default
path excludes shopping list generation per spec.
```

---

## Task 13: Meals MCP Server

Build the FastMCP tool definitions wrapping the service and recommendation engine.

**Files:**
- Create: `minx_mcp/meals/server.py`
- Test: `tests/test_meals_server.py`

- [ ] **Step 1: Write failing tests — in-memory `call_tool` pattern**

`tests/test_meals_server.py`:

```python
import asyncio
import pytest
from pathlib import Path

from minx_mcp.meals.server import create_meals_server
from minx_mcp.meals.service import MealsService


def _call(server, tool_name, args):
    result = asyncio.run(server.call_tool(tool_name, args))
    # call_tool returns list of content blocks; extract structured content
    for block in result:
        if hasattr(block, "data"):
            return block.data
    return result


def test_meal_log_tool(db_path, tmp_path):
    svc = MealsService(db_path, vault_root=tmp_path)
    server = create_meals_server(svc)
    result = _call(server, "meal_log", {
        "meal_kind": "lunch",
        "occurred_at": "2026-04-12T12:00:00Z",
        "summary": "Chicken salad",
    })
    assert result["success"] is True
    assert result["data"]["meal"]["meal_kind"] == "lunch"


def test_meal_log_validates_kind(db_path, tmp_path):
    svc = MealsService(db_path, vault_root=tmp_path)
    server = create_meals_server(svc)
    result = _call(server, "meal_log", {
        "meal_kind": "invalid",
        "occurred_at": "2026-04-12T12:00:00Z",
    })
    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_pantry_add_tool(db_path, tmp_path):
    svc = MealsService(db_path, vault_root=tmp_path)
    server = create_meals_server(svc)
    result = _call(server, "pantry_add", {
        "display_name": "Eggs",
        "quantity": 12,
        "unit": "count",
    })
    assert result["success"] is True
    assert result["data"]["item"]["normalized_name"] == "eggs"


def test_pantry_list_tool(db_path, tmp_path):
    svc = MealsService(db_path, vault_root=tmp_path)
    server = create_meals_server(svc)
    _call(server, "pantry_add", {"display_name": "Milk", "quantity": 1, "unit": "L"})
    result = _call(server, "pantry_list", {})
    assert result["success"] is True
    assert len(result["data"]["items"]) == 1


def test_recommend_recipes_tool(db_path, tmp_path):
    svc = MealsService(db_path, vault_root=tmp_path)
    server = create_meals_server(svc)
    result = _call(server, "recommend_recipes", {})
    assert result["success"] is True
    assert "recommendations" in result["data"]
    assert result["data"]["shopping_lists_generated"] == []


def test_recipe_index_tool(db_path, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    recipes_dir = vault / "Recipes"
    recipes_dir.mkdir()
    (recipes_dir / "Test.md").write_text(
        "---\ntitle: Test\n---\n## Ingredients\n- 1 egg\n"
    )
    svc = MealsService(db_path, vault_root=vault)
    server = create_meals_server(svc)
    result = _call(server, "recipe_index", {"vault_path": "Recipes/Test.md"})
    assert result["success"] is True
    assert result["data"]["recipe"]["title"] == "Test"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_meals_server.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `meals/server.py`**

Follow Finance `server.py` pattern exactly:

```python
from __future__ import annotations

from typing import Protocol
from mcp.server.fastmcp import FastMCP
from minx_mcp.contracts import wrap_tool_call


class MealsServiceLike(Protocol):
    # Define protocol methods matching MealsService public API
    ...


def create_meals_server(service: MealsServiceLike) -> FastMCP:
    mcp = FastMCP("minx-meals", stateless_http=True, json_response=True)

    @mcp.tool(name="meal_log")
    def meal_log(
        meal_kind: str,
        occurred_at: str,
        summary: str | None = None,
        food_items: list[dict] | None = None,
        protein_grams: float | None = None,
        calories: int | None = None,
    ) -> dict[str, object]:
        return wrap_tool_call(lambda: _meal_log(service, ...))

    @mcp.tool(name="pantry_add")
    def pantry_add(...) -> dict[str, object]:
        ...

    @mcp.tool(name="pantry_update")
    def pantry_update(...) -> dict[str, object]:
        ...

    @mcp.tool(name="pantry_remove")
    def pantry_remove(...) -> dict[str, object]:
        ...

    @mcp.tool(name="pantry_list")
    def pantry_list() -> dict[str, object]:
        ...

    @mcp.tool(name="recipe_index")
    def recipe_index(vault_path: str) -> dict[str, object]:
        ...

    @mcp.tool(name="recipe_scan")
    def recipe_scan(directory: str = "Recipes") -> dict[str, object]:
        ...

    @mcp.tool(name="recommend_recipes")
    def recommend_recipes(include_needs_shopping: bool = False) -> dict[str, object]:
        ...

    return mcp
```

All tools use `wrap_tool_call()`. Input validation mirrors Finance patterns.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_meals_server.py -v`
Expected: All pass

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: All pass

- [ ] **Step 6: Commit**

```
feat: add Meals MCP server with tool definitions

FastMCP tools: meal_log, pantry_add, pantry_update, pantry_remove,
pantry_list, recipe_index, recipe_scan, recommend_recipes. In-memory
call_tool tests per FastMCP pattern.
```

---

## Task 14: Entry Point, Packaging, and Launcher

Wire up `__main__.py`, add the entry point to `pyproject.toml`, and add a minimal launcher.

**Files:**
- Create: `minx_mcp/meals/__main__.py`
- Create: `minx_mcp/launcher.py`
- Modify: `pyproject.toml`
- Test: `tests/test_meals_server.py` (add smoke test)

- [ ] **Step 1: Create `meals/__main__.py`**

Mirror `finance/__main__.py`:

```python
from __future__ import annotations

import argparse

from minx_mcp.config import get_settings
from minx_mcp.meals.server import create_meals_server
from minx_mcp.meals.service import MealsService
from minx_mcp.transport import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def main() -> None:
    settings = get_settings()
    args = build_parser().parse_args()
    service = MealsService(settings.db_path, vault_root=settings.vault_path)
    server = create_meals_server(service)
    run_server(
        server,
        transport=args.transport or settings.default_transport,
        host=args.host or settings.http_host,
        port=args.port or settings.http_port,
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add entry point to `pyproject.toml`**

Under `[project.scripts]`, add:

```toml
minx-meals = "minx_mcp.meals.__main__:main"
```

- [ ] **Step 3: Create minimal `minx_mcp/launcher.py`**

The launcher is process supervision only — it starts child processes and relays signals:

```python
from __future__ import annotations

import argparse
import subprocess
import signal
import sys
from pathlib import Path


SERVERS = [
    {"name": "minx-core", "module": "minx_mcp.core"},
    {"name": "minx-finance", "module": "minx_mcp.finance"},
    {"name": "minx-meals", "module": "minx_mcp.meals"},
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Minx MCP launcher")
    parser.add_argument(
        "--servers",
        nargs="*",
        default=[s["name"] for s in SERVERS],
        help="Servers to launch (default: all)",
    )
    parser.add_argument("--transport", default="stdio")
    args = parser.parse_args()

    selected = [s for s in SERVERS if s["name"] in args.servers]
    procs: list[subprocess.Popen] = []

    def _shutdown(signum, frame):
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=5)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    for server in selected:
        proc = subprocess.Popen(
            [sys.executable, "-m", server["module"], "--transport", args.transport],
        )
        procs.append(proc)
        print(f"Started {server['name']} (pid {proc.pid})")

    for p in procs:
        p.wait()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write smoke test — verify Meals server starts**

Add to `tests/test_meals_server.py`:

```python
def test_meals_server_registers_expected_tools(db_path, tmp_path):
    svc = MealsService(db_path, vault_root=tmp_path)
    server = create_meals_server(svc)
    tool_names = [t.name for t in asyncio.run(server.list_tools())]
    assert "meal_log" in tool_names
    assert "pantry_add" in tool_names
    assert "pantry_list" in tool_names
    assert "recommend_recipes" in tool_names
    assert "recipe_index" in tool_names
    assert "recipe_scan" in tool_names


def test_launcher_server_manifest():
    from minx_mcp.launcher import SERVERS

    names = [s["name"] for s in SERVERS]
    assert "minx-core" in names
    assert "minx-finance" in names
    assert "minx-meals" in names
    # Each server has a module that can be imported
    for server in SERVERS:
        assert "module" in server
```

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: All pass

- [ ] **Step 6: Run mypy**

Run: `.venv/bin/python -m mypy minx_mcp/`
Expected: 0 issues

- [ ] **Step 7: Commit**

```
feat: add Meals entry point, launcher, and packaging

minx-meals entry point mirrors finance/__main__.py. Minimal
process launcher can start all three MCP servers together.
pyproject.toml includes minx-meals script.
```

---

## Task 15: Final Integration and Acceptance Tests

End-to-end test that exercises the full pipeline: log meals, add pantry items, index recipes, get recommendations, verify Core snapshot includes nutrition.

**Files:**
- Test: `tests/test_meals_integration.py`

- [ ] **Step 1: Write integration test**

`tests/test_meals_integration.py`:

```python
import pytest
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.core.models import SnapshotContext
from minx_mcp.meals.service import MealsService
from minx_mcp.meals.read_api import MealsReadAPI
from minx_mcp.meals.recommendations import recommend_recipes
from minx_mcp.db import get_connection


@pytest.mark.asyncio
async def test_full_meals_pipeline(tmp_path):
    db_path = tmp_path / "minx.db"
    vault = tmp_path / "vault"
    vault.mkdir()
    recipes_dir = vault / "Recipes"
    recipes_dir.mkdir()

    # Write recipe notes
    (recipes_dir / "Quick Pasta.md").write_text(
        "---\ntitle: Quick Pasta\ntags: [dinner]\n---\n"
        "## Ingredients\n- 400g pasta\n- 2 cups spinach\n- olive oil\n"
    )
    (recipes_dir / "Grilled Salmon.md").write_text(
        "---\ntitle: Grilled Salmon\ntags: [dinner]\n---\n"
        "## Ingredients\n- 1 salmon fillet\n- 1 lemon\n- dill\n"
    )

    svc = MealsService(db_path, vault_root=vault)
    with svc:
        # Log meals
        svc.log_meal(
            occurred_at="2026-04-12T08:00:00Z",
            meal_kind="breakfast",
            protein_grams=30.0,
            calories=450,
        )
        svc.log_meal(
            occurred_at="2026-04-12T12:30:00Z",
            meal_kind="lunch",
            protein_grams=40.0,
            calories=700,
        )

        # Stock pantry
        svc.add_pantry_item(display_name="Pasta", quantity=500, unit="g")
        svc.add_pantry_item(display_name="Spinach", quantity=200, unit="g",
                            expiration_date="2026-04-14")
        svc.add_pantry_item(display_name="Olive Oil", quantity=1, unit="bottle")

        # Index recipes
        svc.scan_vault_recipes()

    # Verify recommendations
    conn = get_connection(db_path)
    result = recommend_recipes(conn, as_of="2026-04-12")
    assert len(result.recommendations) == 2
    # Quick Pasta should rank first (make_now, uses expiring spinach)
    assert result.recommendations[0].recipe_title == "Quick Pasta"
    assert result.recommendations[0].availability_class == "make_now"
    assert result.recommendations[0].expiring_ingredient_hits >= 1
    # Grilled Salmon needs shopping
    assert result.recommendations[1].availability_class == "needs_shopping"
    assert result.shopping_lists_generated == []

    # Verify Core nutrition snapshot
    meals_api = MealsReadAPI(conn)
    nutrition = meals_api.get_nutrition_summary("2026-04-12")
    assert nutrition.meal_count == 2
    assert nutrition.protein_grams == 70.0
    assert nutrition.calories == 1150

    # Verify Core daily snapshot includes nutrition
    ctx = SnapshotContext(db_path=db_path, finance_api=None)
    snapshot = await build_daily_snapshot("2026-04-12", ctx)
    assert snapshot.nutrition is not None
    assert snapshot.nutrition.meal_count == 2

    conn.close()


@pytest.mark.asyncio
async def test_nutrition_detector_fires_in_snapshot(tmp_path):
    db_path = tmp_path / "minx.db"
    svc = MealsService(db_path)
    with svc:
        # Log only dinner with low protein
        svc.log_meal(
            occurred_at="2026-04-12T19:00:00Z",
            meal_kind="dinner",
            protein_grams=25.0,
            calories=600,
        )

    ctx = SnapshotContext(db_path=db_path, finance_api=None)
    snapshot = await build_daily_snapshot("2026-04-12", ctx)

    # Should fire low protein and skipped meals detectors
    signal_types = [s.insight_type for s in snapshot.signals]
    assert "nutrition.low_protein" in signal_types
    assert "nutrition.skipped_meals" in signal_types
```

- [ ] **Step 2: Run integration tests**

Run: `.venv/bin/python -m pytest tests/test_meals_integration.py -v`
Expected: All pass

- [ ] **Step 3: Run full suite and mypy**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m mypy minx_mcp/`
Expected: All tests pass, mypy clean

- [ ] **Step 4: Commit**

```
feat: add Meals integration tests — full pipeline acceptance

End-to-end test: meal logging → pantry stocking → recipe indexing →
recommendation → Core nutrition snapshot → detector signals. Verifies
spec acceptance criteria including expiring-item ranking priority
and no shopping list side effects.
```

---

## Summary

| Task | Phase | What it delivers | Dependencies |
|------|-------|-----------------|--------------|
| 1 | 0 | Per-domain event registry | None |
| 2 | 1 | Meals schema | None |
| 3 | 1 | Domain models | None |
| 4 | 1 | Meals event payloads | Task 1 |
| 5 | 1 | Service (logging + pantry) | Tasks 2, 3, 4 |
| 6 | 1 | Read API + NutritionSnapshot | Tasks 2, 3, 4 |
| 7 | 1 | Nutrition detectors | Task 6 |
| 8 | 2 | Recipe parsing | Task 3 |
| 9 | 2 | Recipe indexing | Tasks 5, 8 |
| 10 | 2 | Pantry matching | Tasks 2, 3 |
| 11 | 2 | Classification + ranking | Tasks 3, 10 |
| 12 | 2 | E2E recommendation | Tasks 9, 10, 11 |
| 13 | 2 | MCP server tools | Tasks 5, 12 |
| 14 | 0+pkg | Entry point + launcher | Task 13 |
| 15 | all | Integration acceptance | All above |

**Parallelizable groups:** Tasks 1/2/3 can run in parallel. Tasks 6/8/10 can run in parallel after their deps. Task 7 can run in parallel with 8-10.

**Expected test count increase:** ~80-100 new tests, bringing total from 468 to ~550-570.

**Notes:**
- Task 5's `add_pantry_item()` uses simple lowercase+strip normalization initially. When Task 10 lands `normalize_ingredient()` in `pantry.py`, update the service to call that function instead.
- The `db_path` fixture returns a raw path without initializing the DB. `MealsService.__init__` must lazily call `get_connection()` (same as `FinanceService`).
- `MealsService(db_path)` without `vault_root` is valid — recipe-related methods should raise `InvalidInputError` if called without a vault configured.
