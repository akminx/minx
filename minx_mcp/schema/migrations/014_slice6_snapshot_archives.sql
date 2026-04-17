-- Slice 6b: Tier-2 episodic snapshot archives (JSON + content hash)

CREATE TABLE IF NOT EXISTS snapshot_archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_date TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    snapshot_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'build_daily_snapshot',
    UNIQUE (review_date, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_snapshot_archives_review_date
    ON snapshot_archives(review_date);

CREATE INDEX IF NOT EXISTS idx_snapshot_archives_generated_at
    ON snapshot_archives(generated_at);
