-- Meals nutrition profile + target metadata

CREATE TABLE meals_nutrition_profiles (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    sex TEXT NOT NULL,
    age_years INTEGER NOT NULL,
    height_cm REAL NOT NULL,
    weight_kg REAL NOT NULL,
    activity_level TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT 'fat_loss',
    calorie_deficit_kcal INTEGER NOT NULL DEFAULT 400,
    protein_g_per_kg REAL NOT NULL DEFAULT 2.0,
    fat_g_per_kg REAL NOT NULL DEFAULT 0.77,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE meals_nutrition_targets (
    profile_id INTEGER PRIMARY KEY REFERENCES meals_nutrition_profiles(id) ON DELETE CASCADE,
    bmr_kcal INTEGER NOT NULL,
    tdee_kcal INTEGER NOT NULL,
    calorie_target_kcal INTEGER NOT NULL,
    protein_target_grams INTEGER NOT NULL,
    fat_target_grams INTEGER NOT NULL,
    carbs_target_grams INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
