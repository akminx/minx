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

