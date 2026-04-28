-- Slice 9: durable investigation lifecycle and render-event audit trail.
CREATE TABLE investigations (
    id INTEGER PRIMARY KEY,
    harness TEXT NOT NULL,
    kind TEXT NOT NULL
        CHECK (kind IN ('investigate', 'plan', 'retro', 'onboard', 'other')),
    question TEXT NOT NULL,
    context_json TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled', 'budget_exhausted')),
    answer_md TEXT,
    trajectory_json TEXT NOT NULL DEFAULT '[]',
    response_template TEXT,
    response_slots_json TEXT,
    citation_refs_json TEXT,
    tool_call_count INTEGER,
    token_input INTEGER,
    token_output INTEGER,
    cost_usd REAL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);

CREATE INDEX idx_investigations_kind_started
ON investigations(kind, started_at DESC);

CREATE INDEX idx_investigations_harness_started
ON investigations(harness, started_at DESC);

CREATE INDEX idx_investigations_status_started
ON investigations(status, started_at DESC);

CREATE INDEX idx_investigations_running
ON investigations(status)
WHERE status = 'running';
