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
