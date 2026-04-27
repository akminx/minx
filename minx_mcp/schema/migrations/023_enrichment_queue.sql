-- Slice 6k: durable enrichment queue.

CREATE TABLE IF NOT EXISTS enrichment_jobs (
    id INTEGER PRIMARY KEY,
    job_type TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'dead_letter')),
    priority INTEGER NOT NULL DEFAULT 100,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    available_at TEXT NOT NULL DEFAULT (datetime('now')),
    locked_at TEXT,
    completed_at TEXT,
    last_error TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (attempts >= 0),
    CHECK (max_attempts >= 1)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_jobs_status_available
    ON enrichment_jobs(status, available_at, priority, id);

CREATE INDEX IF NOT EXISTS idx_enrichment_jobs_subject
    ON enrichment_jobs(subject_type, subject_id);
