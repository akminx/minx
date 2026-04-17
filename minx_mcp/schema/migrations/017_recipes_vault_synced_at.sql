-- 017_recipes_vault_synced_at.sql
-- Track last confirmed vault presence for each recipe row so reconcile
-- can identify orphans (DB rows whose vault_path no longer exists).
--
-- SQLite cannot relax NOT NULL on vault_path via ALTER alone; rebuild
-- meals_recipes (and dependent tables) so vault_path may be NULL for soft-delete.

CREATE TABLE meals_recipes_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_path TEXT UNIQUE,
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
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    vault_synced_at TEXT NULL
);

INSERT INTO meals_recipes_new (
    id,
    vault_path,
    title,
    normalized_title,
    source_url,
    image_ref,
    prep_time_minutes,
    cook_time_minutes,
    servings,
    tags_json,
    notes,
    nutrition_summary_json,
    content_hash,
    indexed_at,
    created_at,
    updated_at,
    vault_synced_at
)
SELECT
    id,
    vault_path,
    title,
    normalized_title,
    source_url,
    image_ref,
    prep_time_minutes,
    cook_time_minutes,
    servings,
    tags_json,
    notes,
    nutrition_summary_json,
    content_hash,
    indexed_at,
    created_at,
    updated_at,
    NULL
FROM meals_recipes;

CREATE TABLE meals_recipe_ingredients_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES meals_recipes_new(id) ON DELETE CASCADE,
    display_text TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    quantity REAL,
    unit TEXT,
    is_required INTEGER NOT NULL DEFAULT 1,
    ingredient_group TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

INSERT INTO meals_recipe_ingredients_new SELECT * FROM meals_recipe_ingredients;

CREATE TABLE meals_recipe_substitutions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_ingredient_id INTEGER NOT NULL REFERENCES meals_recipe_ingredients_new(id) ON DELETE CASCADE,
    substitute_normalized_name TEXT NOT NULL,
    display_text TEXT NOT NULL,
    quantity REAL,
    unit TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

INSERT INTO meals_recipe_substitutions_new SELECT * FROM meals_recipe_substitutions;

DROP TABLE meals_recipe_substitutions;
DROP TABLE meals_recipe_ingredients;
DROP TABLE meals_recipes;

ALTER TABLE meals_recipes_new RENAME TO meals_recipes;
ALTER TABLE meals_recipe_ingredients_new RENAME TO meals_recipe_ingredients;
ALTER TABLE meals_recipe_substitutions_new RENAME TO meals_recipe_substitutions;

CREATE INDEX IF NOT EXISTS idx_meals_ingredients_recipe ON meals_recipe_ingredients (recipe_id);
CREATE INDEX IF NOT EXISTS idx_meals_ingredients_normalized ON meals_recipe_ingredients (normalized_name);
CREATE INDEX IF NOT EXISTS idx_meals_substitutions_ingredient ON meals_recipe_substitutions (recipe_ingredient_id);

CREATE INDEX IF NOT EXISTS idx_meals_recipes_vault_synced_at ON meals_recipes (vault_synced_at);
