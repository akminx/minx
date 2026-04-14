# Slice 3 Phase 3 Shopping List Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit shopping list generation for selected Meals recipes without changing the default recommendation path.

**Architecture:** Meals remains the owner of recipe, pantry, substitution, and shopping-list business rules. Phase 3 adds persisted shopping-list artifact metadata in SQLite, optional Obsidian markdown artifact writing when a vault is configured, and a FastMCP tool for explicit generation. Recommendations must continue to be side-effect free and must keep `shopping_lists_generated` empty unless a later phase deliberately changes that contract.

**Tech Stack:** Python 3.12, FastMCP, SQLite3 migrations, pytest, mypy

**Spec:** `docs/superpowers/specs/2026-04-11-slice3-meals-mcp-design.md`

---

## Current State

Slice 3 Phases 0, 1, and 2 are merged on `main` through PR #2. The current working tree was clean before this handoff document was added.

Implemented and ready:

- Meals MCP domain server and `minx-meals` entry point.
- Meals schema migration `010_meals.sql` in both packaged and mirror migration directories.
- Meal logging, pantry CRUD, recipe indexing, recipe scanning, recommendation ranking, substitutions, low-stock and expiring-item signals.
- Core nutrition snapshot support and nutrition detectors.
- Recommendation output explicitly includes `shopping_lists_generated=[]`.

Still remaining for Slice 3:

- Phase 3: shopping list generation and low-stock replenishment support.
- Phase 4: richer recipe presentation and attachments.

Phase 3 is the next useful Slice 3 step. Phase 4 can wait unless the harness is blocked on richer recipe cards immediately after Phase 3.

## Phase 4 Decision

Do Phase 3 now.

Defer Phase 4 unless the user explicitly wants richer recipe cards before Slice 4. The important Phase 4 data fields already exist in `Recipe` and `RecipeMetadata`: `vault_path`, `image_ref`, `tags`, and `source_url`; the recipe row also stores prep time, cook time, servings, notes, and nutrition summary. That means postponing Phase 4 is low risk. It is presentation polish and response shaping, not core business logic.

Do Phase 4 immediately only if the next harness workflow needs one of these before Training MCP work:

- recipe detail responses with image/source/prep/cook/servings surfaced as a dedicated tool
- richer card-ready response payloads
- attachment/path handling that the UI cannot reasonably infer from current recommendation metadata

## Non-Goals

Do not implement these in Phase 3:

- automatic shopping list side effects from `recommend_recipes`
- HEB automation
- browser-driven shopping checkout
- full SousChef parity
- LLM-first meal generation
- unit conversion beyond exact unit matches
- recursive recipe/sub-recipe shopping diffs

## File Structure

### Create

| File | Responsibility |
|------|----------------|
| `minx_mcp/meals/shopping.py` | Pure shopping-list diff logic: required ingredients, pantry quantity comparison, substitution coverage. |
| `minx_mcp/schema/migrations/011_meals_shopping_lists.sql` | Runtime packaged migration for shopping-list tables. |
| `schema/migrations/011_meals_shopping_lists.sql` | Mirror migration; content must match packaged migration. |
| `tests/test_meals_shopping.py` | Pure diff and service-level shopping list tests. |

### Modify

| File | Change |
|------|--------|
| `minx_mcp/meals/models.py` | Add `ShoppingList` and `ShoppingListItem` dataclasses. |
| `minx_mcp/meals/service.py` | Add shopping-list creation, retrieval, row mappers, and optional Obsidian artifact writing. |
| `minx_mcp/meals/server.py` | Add explicit `shopping_list_generate(recipe_id)` FastMCP tool. |
| `tests/helpers.py` | Add optional helper methods for shopping-list assertions only if the tests need direct DB setup. Prefer service calls first. |
| `tests/test_db.py` | Add migration/table coverage and wheel packaging assertion for `011_meals_shopping_lists.sql`. |
| `tests/test_migration_checksums.py` | Update latest-migration ordering assertion so `011` does not break checksum suite. |
| `tests/test_meals_recommendations.py` | Keep and expand the no-side-effects assertion around recommendations. |
| `tests/test_meals_integration.py` | Add end-to-end explicit shopping-list generation after recommendation selection. |
| `tests/test_meals_server.py` | Add in-memory FastMCP test for the new tool. |

## Data Model

Add `011_meals_shopping_lists.sql` to both migration directories:

```sql
-- Meals shopping list generated artifacts (Slice 3, Phase 3)

CREATE TABLE meals_shopping_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES meals_recipes(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    vault_path TEXT,
    status TEXT NOT NULL DEFAULT 'generated',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_meals_shopping_lists_recipe ON meals_shopping_lists(recipe_id);
CREATE INDEX idx_meals_shopping_lists_created ON meals_shopping_lists(created_at);

CREATE TABLE meals_shopping_list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shopping_list_id INTEGER NOT NULL REFERENCES meals_shopping_lists(id) ON DELETE CASCADE,
    recipe_ingredient_id INTEGER REFERENCES meals_recipe_ingredients(id) ON DELETE SET NULL,
    display_text TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    quantity REAL,
    unit TEXT,
    pantry_quantity REAL,
    missing_quantity REAL,
    pantry_unit TEXT,
    notes TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_meals_shopping_list_items_list ON meals_shopping_list_items(shopping_list_id);
CREATE INDEX idx_meals_shopping_list_items_normalized ON meals_shopping_list_items(normalized_name);
```

`status` is intentionally simple. Use `generated` for Phase 3. Do not add purchase workflow states until a later shopping workflow needs them.

`recipe_ingredient_id` is intentionally nullable with `ON DELETE SET NULL` so generated shopping-list artifacts survive recipe re-index flows that replace ingredient rows.

## Quantity And Substitution Rules

Implement the Phase 3 diff rules as a deterministic pure function before wiring service persistence.

Coverage rules:

- Optional ingredients never appear in shopping list diffs.
- A direct pantry item with the same normalized name covers an ingredient when the recipe ingredient has no quantity.
- If recipe and pantry quantities are both known and units match exactly after lowercasing and stripping whitespace, the ingredient is covered when `pantry.quantity >= ingredient.quantity`.
- If recipe and pantry quantities are both known and units match, generate a shopping-list item for `ingredient.quantity - pantry.quantity` when the pantry quantity is lower.
- If either side lacks quantity or units do not match, treat the direct pantry match as present and do not generate a shopping-list item. Phase 3 should avoid false-positive "missing" claims when it cannot compare units safely.
- A substitution covers a required ingredient when substitutions are evaluated in `(priority, id)` order and the first pantry-covered substitution short-circuits coverage (equivalent to "any covered substitution" with deterministic tie behavior).
- If a substitution covers the ingredient, neither the original ingredient nor the substitute appears in the shopping list.
- If no direct pantry item and no substitution covers the ingredient, list the original required ingredient.

This is enough to satisfy "quantity-aware pantry diffs" without taking on unit conversion.

## Task 1: Add Shopping List Schema

**Files:**

- Create: `minx_mcp/schema/migrations/011_meals_shopping_lists.sql`
- Create: `schema/migrations/011_meals_shopping_lists.sql`
- Modify: `tests/test_db.py`
- Modify: `tests/test_migration_checksums.py`

- [ ] **Step 1: Add failing migration tests**

Add a database bootstrap test to `tests/test_db.py`:

```python
def test_database_bootstrap_creates_meals_shopping_list_tables(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()

    assert "meals_shopping_lists" in names
    assert "meals_shopping_list_items" in names
```

Extend `test_built_wheel_includes_packaged_migrations` in `tests/test_db.py` with:

```python
assert "minx_mcp/schema/migrations/011_meals_shopping_lists.sql" in names
```

Update the fixed migration-count assertions in `tests/test_db.py` so they stay correct when `011` is added:

```python
expected = len(list(migration_dir().glob("*.sql")))
assert count == expected
```

Apply this to both:

- `test_migrations_are_idempotent`
- `test_apply_migrations_handles_plain_sqlite_connections`

Update `tests/test_migration_checksums.py` migration-order assertion so adding `011` does not break the checksum suite:

```python
latest = sorted(path.name for path in migration_dir().glob("*.sql"))[-1]
assert names[-1] == latest
```

Apply this to the current `test_010_migration_recorded_in_order` (renaming it to `test_latest_migration_recorded_in_order` is recommended but optional).

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
uv run pytest tests/test_db.py::test_database_bootstrap_creates_meals_shopping_list_tables tests/test_db.py::test_built_wheel_includes_packaged_migrations -q
uv run pytest tests/test_migration_checksums.py -k migration_recorded_in_order -q
```

Expected: the new table test fails because `011_meals_shopping_lists.sql` does not exist yet. The migration-order test should still pass once it is made dynamic.

- [ ] **Step 3: Add matching migration files**

Create `minx_mcp/schema/migrations/011_meals_shopping_lists.sql` and `schema/migrations/011_meals_shopping_lists.sql` with the exact SQL from the Data Model section.

- [ ] **Step 4: Run migration tests**

Run:

```bash
uv run pytest tests/test_db.py::test_database_bootstrap_creates_meals_shopping_list_tables tests/test_db.py::test_built_wheel_includes_packaged_migrations -q
uv run pytest tests/test_migration_checksums.py -k migration_recorded_in_order -q
```

Expected: both tests pass.

## Task 2: Add Models And Pure Diff Logic

**Files:**

- Modify: `minx_mcp/meals/models.py`
- Create: `minx_mcp/meals/shopping.py`
- Test: `tests/test_meals_shopping.py`

- [ ] **Step 1: Write failing diff tests**

Create `tests/test_meals_shopping.py`:

```python
from __future__ import annotations

from minx_mcp.meals.models import PantryItem, Recipe, RecipeIngredient, RecipeSubstitution
from minx_mcp.meals.shopping import missing_shopping_items


def _ingredient(
    *,
    ingredient_id: int,
    display_text: str,
    normalized_name: str,
    quantity: float | None = None,
    unit: str | None = None,
    is_required: bool = True,
) -> RecipeIngredient:
    return RecipeIngredient(
        id=ingredient_id,
        recipe_id=1,
        display_text=display_text,
        normalized_name=normalized_name,
        quantity=quantity,
        unit=unit,
        is_required=is_required,
        ingredient_group=None,
        sort_order=ingredient_id,
        notes=None,
    )


def _recipe(ingredients: list[RecipeIngredient], substitutions: list[RecipeSubstitution] | None = None) -> Recipe:
    return Recipe(
        id=1,
        vault_path="Recipes/Test.md",
        title="Test Recipe",
        normalized_title="test recipe",
        source_url=None,
        image_ref=None,
        prep_time_minutes=None,
        cook_time_minutes=None,
        servings=None,
        tags=[],
        notes=None,
        nutrition_summary=None,
        content_hash="abc123",
        ingredients=ingredients,
        substitutions=substitutions or [],
    )


def _pantry(name: str, quantity: float | None = None, unit: str | None = None) -> PantryItem:
    return PantryItem(
        id=1,
        display_name=name.title(),
        normalized_name=name,
        quantity=quantity,
        unit=unit,
        expiration_date=None,
        low_stock_threshold=None,
        source="test",
    )


def test_missing_shopping_items_include_required_missing_only() -> None:
    required = _ingredient(ingredient_id=1, display_text="200g salmon", normalized_name="salmon", quantity=200, unit="g")
    optional = _ingredient(ingredient_id=2, display_text="lemon wedge", normalized_name="lemon", is_required=False)

    items = missing_shopping_items(_recipe([required, optional]), pantry_items=[])

    assert [item.ingredient.normalized_name for item in items] == ["salmon"]


def test_missing_shopping_items_diff_against_pantry_quantity() -> None:
    pasta = _ingredient(ingredient_id=1, display_text="400g pasta", normalized_name="pasta", quantity=400, unit="g")

    items = missing_shopping_items(_recipe([pasta]), pantry_items=[_pantry("pasta", quantity=150, unit="g")])

    assert len(items) == 1
    assert items[0].missing_quantity == 250
    assert items[0].pantry_quantity == 150


def test_missing_shopping_items_exclude_covered_substitution() -> None:
    chickpeas = _ingredient(ingredient_id=1, display_text="1 cup chickpeas", normalized_name="chickpea", quantity=1, unit="cup")
    substitution = RecipeSubstitution(
        id=1,
        recipe_ingredient_id=1,
        substitute_normalized_name="white bean",
        display_text="white beans",
        quantity=None,
        unit=None,
        priority=0,
        notes=None,
    )

    items = missing_shopping_items(_recipe([chickpeas], [substitution]), pantry_items=[_pantry("white bean")])

    assert items == []
```

- [ ] **Step 2: Run the tests and confirm import failure**

Run:

```bash
uv run pytest tests/test_meals_shopping.py -q
```

Expected: failure because `minx_mcp.meals.shopping` does not exist.

- [ ] **Step 3: Add shopping dataclasses**

Add to `minx_mcp/meals/models.py`:

```python
@dataclass(frozen=True)
class ShoppingListItem:
    id: int
    shopping_list_id: int
    recipe_ingredient_id: int | None
    display_text: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    pantry_quantity: float | None
    missing_quantity: float | None
    pantry_unit: str | None
    notes: str | None
    sort_order: int


@dataclass(frozen=True)
class ShoppingList:
    id: int
    recipe_id: int
    recipe_title: str
    title: str
    vault_path: str | None
    status: str
    created_at: str
    items: list[ShoppingListItem] = field(default_factory=list)
```

- [ ] **Step 4: Add `minx_mcp/meals/shopping.py`**

Create:

```python
from __future__ import annotations

from dataclasses import dataclass

from minx_mcp.meals.models import PantryItem, Recipe, RecipeIngredient


@dataclass(frozen=True)
class ShoppingItemDraft:
    ingredient: RecipeIngredient
    pantry_quantity: float | None
    missing_quantity: float | None
    pantry_unit: str | None
    notes: str | None


def missing_shopping_items(recipe: Recipe, pantry_items: list[PantryItem]) -> list[ShoppingItemDraft]:
    pantry_by_name = {item.normalized_name: item for item in pantry_items}
    substitution_map = _substitution_map(recipe)
    drafts: list[ShoppingItemDraft] = []
    for ingredient in recipe.ingredients:
        if not ingredient.is_required:
            continue
        direct = pantry_by_name.get(ingredient.normalized_name)
        direct_coverage = _coverage(ingredient, direct)
        if direct_coverage.covered:
            continue
        if _has_covering_substitution(ingredient, substitution_map.get(ingredient.id, []), pantry_by_name):
            continue
        drafts.append(
            ShoppingItemDraft(
                ingredient=ingredient,
                pantry_quantity=direct.quantity if direct is not None else None,
                missing_quantity=direct_coverage.missing_quantity,
                pantry_unit=direct.unit if direct is not None else None,
                notes=direct_coverage.notes,
            )
        )
    return drafts


@dataclass(frozen=True)
class _Coverage:
    covered: bool
    missing_quantity: float | None
    notes: str | None


def _coverage(ingredient: RecipeIngredient, pantry_item: PantryItem | None) -> _Coverage:
    if pantry_item is None:
        return _Coverage(covered=False, missing_quantity=ingredient.quantity, notes="not in pantry")
    if ingredient.quantity is None:
        return _Coverage(covered=True, missing_quantity=None, notes=None)
    if pantry_item.quantity is None:
        return _Coverage(covered=True, missing_quantity=None, notes="pantry quantity unknown")
    if not _same_unit(ingredient.unit, pantry_item.unit):
        return _Coverage(covered=True, missing_quantity=None, notes="pantry unit not comparable")
    missing = ingredient.quantity - pantry_item.quantity
    if missing <= 0:
        return _Coverage(covered=True, missing_quantity=None, notes=None)
    return _Coverage(covered=False, missing_quantity=missing, notes="pantry quantity below recipe quantity")


def _has_covering_substitution(
    ingredient: RecipeIngredient,
    substitution_names: list[str],
    pantry_by_name: dict[str, PantryItem],
) -> bool:
    for substitute_name in substitution_names:
        substitute_item = pantry_by_name.get(substitute_name)
        if _coverage(ingredient, substitute_item).covered:
            return True
    return False


def _substitution_map(recipe: Recipe) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for substitution in sorted(recipe.substitutions, key=lambda sub: (sub.priority, sub.id)):
        result.setdefault(substitution.recipe_ingredient_id, []).append(substitution.substitute_normalized_name)
    return result


def _same_unit(left: str | None, right: str | None) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()
```

- [ ] **Step 5: Run focused shopping tests**

Run:

```bash
uv run pytest tests/test_meals_shopping.py -q
```

Expected: all shopping diff tests pass.

## Task 3: Persist And Render Shopping Lists

**Files:**

- Modify: `minx_mcp/meals/service.py`
- Modify: `tests/test_meals_shopping.py`

- [ ] **Step 1: Add failing service tests**

Append to `tests/test_meals_shopping.py`:

```python
import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.meals.service import MealsService


def test_generate_shopping_list_persists_missing_required_items(db_path, meals_seeder) -> None:
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Salmon.md", title="Salmon Dinner")
    meals_seeder.recipe_ingredient(
        recipe_id=recipe_id,
        display_text="200g salmon",
        normalized_name="salmon",
        quantity=200,
        unit="g",
    )
    meals_seeder.pantry_item(display_name="Salmon", normalized_name="salmon", quantity=50, unit="g")

    with MealsService(db_path) as service:
        shopping_list = service.generate_shopping_list(recipe_id)

    assert shopping_list.recipe_id == recipe_id
    assert shopping_list.recipe_title == "Salmon Dinner"
    assert shopping_list.vault_path is None
    assert [(item.normalized_name, item.missing_quantity, item.unit) for item in shopping_list.items] == [
        ("salmon", 150.0, "g")
    ]


def test_generate_shopping_list_rejects_recipe_without_missing_required_items(db_path, meals_seeder) -> None:
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Pasta.md", title="Pantry Pasta")
    meals_seeder.recipe_ingredient(recipe_id=recipe_id, display_text="400g pasta", normalized_name="pasta", quantity=400, unit="g")
    meals_seeder.pantry_item(display_name="Pasta", normalized_name="pasta", quantity=500, unit="g")

    with pytest.raises(InvalidInputError, match="does not need a shopping list"):
        with MealsService(db_path) as service:
            service.generate_shopping_list(recipe_id)


def test_generate_shopping_list_writes_vault_artifact_when_vault_configured(db_path, tmp_path, meals_seeder) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Salmon.md", title="Salmon Dinner")
    meals_seeder.recipe_ingredient(recipe_id=recipe_id, display_text="200g salmon", normalized_name="salmon", quantity=200, unit="g")

    with MealsService(db_path, vault_root=vault) as service:
        shopping_list = service.generate_shopping_list(recipe_id)

    assert shopping_list.vault_path is not None
    artifact = vault / shopping_list.vault_path
    assert artifact.exists()
    assert "Salmon Dinner" in artifact.read_text()
    assert "200g salmon" in artifact.read_text()
```

- [ ] **Step 2: Run service tests and confirm method failure**

Run:

```bash
uv run pytest tests/test_meals_shopping.py -q
```

Expected: failure because `MealsService.generate_shopping_list` does not exist.

- [ ] **Step 3: Add service methods and row mappers**

In `minx_mcp/meals/service.py`:

- import `ShoppingList`, `ShoppingListItem`
- import `missing_shopping_items`
- add `generate_shopping_list(self, recipe_id: int) -> ShoppingList`
- add `get_shopping_list(self, shopping_list_id: int) -> ShoppingList`
- add `_shopping_list_items(self, shopping_list_id: int) -> list[ShoppingListItem]`
- add `_write_shopping_list_artifact(self, shopping_list: ShoppingList) -> str | None`
- add `_shopping_list_from_row(...)` and `_shopping_list_item_from_row(...)`

Behavior to implement:

- load recipe through `get_recipe(recipe_id)`
- compute drafts with `missing_shopping_items(recipe, self.list_pantry_items())`
- raise `InvalidInputError(f"recipe {recipe_id} does not need a shopping list")` when no drafts are produced
- insert a `meals_shopping_lists` row with `title = f"Shopping List: {recipe.title}"`
- insert one `meals_shopping_list_items` row per draft
- commit once after rows and optional artifact path update are complete
- if `self._vault_root` is set, write markdown under `Generated/Shopping Lists/`
- generated list items must remain readable even after recipe re-index replaces ingredient rows (schema-level guarantee via nullable `recipe_ingredient_id`)
- `_shopping_list_item_from_row(...)` must map `recipe_ingredient_id` as optional and safely return `None` when the FK has been nulled by recipe re-index.
- return `get_shopping_list(shopping_list_id)`

Use this markdown shape for the artifact:

```markdown
# Shopping List: Salmon Dinner

Source recipe: Recipes/Salmon.md

- [ ] 200g salmon
```

For quantity deltas, render the missing amount when available:

```markdown
- [ ] 150g salmon
```

For no known quantity, render the original ingredient display text.

- [ ] **Step 4: Run service tests**

Run:

```bash
uv run pytest tests/test_meals_shopping.py -q
```

Expected: all shopping tests pass.

## Task 4: Add The MCP Tool

**Files:**

- Modify: `minx_mcp/meals/server.py`
- Modify: `tests/test_meals_server.py`

- [ ] **Step 1: Add failing server test**

In `tests/test_meals_server.py`, add an in-memory tool test following the existing FastMCP pattern:

```python
def test_shopping_list_generate_tool(db_path, meals_seeder) -> None:
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Salmon.md", title="Salmon Dinner")
    meals_seeder.recipe_ingredient(recipe_id=recipe_id, display_text="200g salmon", normalized_name="salmon", quantity=200, unit="g")
    service = MealsService(db_path)
    server = create_meals_server(service)

    result = _call(server, "shopping_list_generate", {"recipe_id": recipe_id})

    assert result["success"] is True
    assert result["data"]["shopping_list"]["recipe_title"] == "Salmon Dinner"
    assert result["data"]["shopping_list"]["items"][0]["normalized_name"] == "salmon"
```

- [ ] **Step 2: Run server test and confirm missing tool failure**

Run:

```bash
uv run pytest tests/test_meals_server.py::test_shopping_list_generate_tool -q
```

Expected: failure because the tool is not registered yet.

- [ ] **Step 3: Add the tool**

Add this to `create_meals_server()` in `minx_mcp/meals/server.py`:

```python
    @mcp.tool(name="shopping_list_generate")
    def shopping_list_generate(recipe_id: int) -> dict[str, object]:
        return wrap_tool_call(
            lambda: {"shopping_list": asdict(service.generate_shopping_list(recipe_id))}
        )
```

- [ ] **Step 4: Run the focused server test**

Run:

```bash
uv run pytest tests/test_meals_server.py::test_shopping_list_generate_tool -q
```

Expected: the new server test passes.

## Task 5: Preserve Recommendation Side-Effect Contract

**Files:**

- Modify: `tests/test_meals_recommendations.py`
- Modify: `tests/test_meals_integration.py`

- [ ] **Step 1: Expand recommendation guard test**

In `tests/test_meals_recommendations.py`, keep the existing assertion and add a DB assertion after calling `recommend_recipes(..., include_needs_shopping=True)`:

```python
count = db_conn.execute("SELECT COUNT(*) FROM meals_shopping_lists").fetchone()[0]
assert count == 0
```

- [ ] **Step 2: Add explicit integration path**

In `tests/test_meals_integration.py`, after the recommendation assertion, open a `MealsService` and explicitly generate a shopping list for the needs-shopping recipe. Assert that the artifact is generated only after that explicit call. Then re-index the recipe note and assert the generated shopping-list items still exist:

```python
with MealsService(db_path, vault_root=vault) as svc:
    shopping_list = svc.generate_shopping_list(result.recommendations[1].recipe_id)
    svc.index_recipe("Recipes/Grilled Salmon.md")
    reloaded = svc.get_shopping_list(shopping_list.id)

assert shopping_list.recipe_title == "Grilled Salmon"
assert [item.normalized_name for item in shopping_list.items] == ["salmon fillet"]
assert [item.normalized_name for item in reloaded.items] == ["salmon fillet"]
assert all(item.recipe_ingredient_id is None or isinstance(item.recipe_ingredient_id, int) for item in reloaded.items)
```

- [ ] **Step 3: Run integration and recommendation tests**

Run:

```bash
uv run pytest tests/test_meals_recommendations.py tests/test_meals_integration.py -q
```

Expected: all tests pass and recommendation remains side-effect free.

## Task 6: Full Verification

- [ ] **Step 1: Run the Meals-focused suite**

Run:

```bash
uv run pytest tests/test_meals_*.py -q
```

Expected: all Meals tests pass.

- [ ] **Step 2: Run full tests**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Run type checking**

Run:

```bash
uv run mypy
```

Expected: `Success: no issues found`.

- [ ] **Step 4: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

## Final Acceptance

Phase 3 is complete when:

- `recommend_recipes()` still creates no shopping lists and returns `shopping_lists_generated=[]`.
- A shopping list is created only by explicit service/tool request for a selected recipe.
- Required missing ingredients appear.
- Optional ingredients do not appear.
- Ingredients covered by direct pantry quantity do not appear.
- Ingredients covered by substitutions do not appear.
- Quantity-aware same-unit pantry shortfalls produce missing deltas.
- Obsidian artifact writing works when `vault_root` is configured.
- SQLite persistence works when `vault_root` is not configured.
- Generated shopping-list items survive recipe re-index (ingredient row replacement).

After Phase 3, decide whether to continue with Phase 4 or move to Slice 4:

- Move to Slice 4 if the next priority is Training MCP and current recipe recommendation plus shopping-list output is enough.
- Do Phase 4 first if recipe cards/images/source/prep/cook/serving metadata are blocking the harness experience.
