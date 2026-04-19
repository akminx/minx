-- Slice 8a: playbook audit trail for harness-driven autonomy.
CREATE TABLE playbook_runs (
    id INTEGER PRIMARY KEY,
    playbook_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    trigger_type TEXT NOT NULL
        CHECK (trigger_type IN ('cron', 'event', 'manual')),
    trigger_ref TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'skipped', 'succeeded', 'failed')),
    conditions_met INTEGER,
    action_taken INTEGER,
    result_json TEXT,
    error_message TEXT,
    completed_at TEXT
);

CREATE INDEX idx_playbook_runs_playbook_triggered
ON playbook_runs(playbook_id, triggered_at DESC);

CREATE INDEX idx_playbook_runs_status_triggered
ON playbook_runs(status, triggered_at DESC);

-- Important: NULLs are distinct in SQLite UNIQUE indexes.
-- Use COALESCE(trigger_ref, '') so duplicate in-flight rows with NULL refs are blocked.
CREATE UNIQUE INDEX idx_playbook_runs_in_flight
ON playbook_runs(playbook_id, trigger_type, COALESCE(trigger_ref, ''))
WHERE status = 'running';
