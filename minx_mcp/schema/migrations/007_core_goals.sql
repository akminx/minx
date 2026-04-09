CREATE TABLE IF NOT EXISTS goals (
    id            INTEGER PRIMARY KEY,
    goal_type     TEXT NOT NULL,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    metric_type   TEXT NOT NULL,
    target_value  INTEGER NOT NULL,
    period        TEXT NOT NULL,
    domain        TEXT NOT NULL,
    filters_json  TEXT NOT NULL,
    starts_on     TEXT NOT NULL,
    ends_on       TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_goals_status_domain
ON goals(status, domain);

CREATE INDEX IF NOT EXISTS idx_goals_period_status
ON goals(period, status);
