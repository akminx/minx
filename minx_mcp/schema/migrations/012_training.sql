-- Training domain tables (Slice 4)

CREATE TABLE training_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    muscle_group TEXT,
    is_compound INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_training_exercises_normalized ON training_exercises(normalized_name);

CREATE TABLE training_programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    description TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_training_programs_status ON training_programs(is_active, updated_at);

CREATE TABLE training_program_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id INTEGER NOT NULL REFERENCES training_programs(id) ON DELETE CASCADE,
    day_index INTEGER NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(program_id, day_index)
);

CREATE INDEX idx_training_program_days_program ON training_program_days(program_id, day_index);

CREATE TABLE training_program_exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    program_day_id INTEGER NOT NULL REFERENCES training_program_days(id) ON DELETE CASCADE,
    exercise_id INTEGER REFERENCES training_exercises(id) ON DELETE SET NULL,
    display_name TEXT NOT NULL,
    target_sets INTEGER,
    target_reps INTEGER,
    target_rpe REAL,
    notes TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_training_program_exercises_day ON training_program_exercises(program_day_id, sort_order);

CREATE TABLE training_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    program_id INTEGER REFERENCES training_programs(id) ON DELETE SET NULL,
    notes TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_training_sessions_occurred ON training_sessions(occurred_at);

CREATE TABLE training_session_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES training_sessions(id) ON DELETE CASCADE,
    exercise_id INTEGER REFERENCES training_exercises(id) ON DELETE SET NULL,
    display_name TEXT NOT NULL,
    set_index INTEGER NOT NULL,
    reps INTEGER,
    weight_kg REAL,
    rpe REAL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_training_session_sets_session ON training_session_sets(session_id, set_index);
CREATE INDEX idx_training_session_sets_exercise ON training_session_sets(exercise_id);

CREATE TABLE training_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    milestone_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'detector',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_training_milestones_occurred ON training_milestones(occurred_at);
CREATE INDEX idx_training_milestones_type ON training_milestones(milestone_type);
