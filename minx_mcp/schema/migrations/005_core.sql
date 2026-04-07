CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY,
    event_type      TEXT NOT NULL,
    domain          TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    entity_ref      TEXT,
    source          TEXT NOT NULL,
    payload         TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    sensitivity     TEXT NOT NULL DEFAULT 'normal'
);

CREATE INDEX IF NOT EXISTS idx_events_domain_type
ON events(domain, event_type);

CREATE INDEX IF NOT EXISTS idx_events_occurred
ON events(occurred_at);

CREATE TABLE IF NOT EXISTS insights (
    id                  INTEGER PRIMARY KEY,
    insight_type        TEXT NOT NULL,
    dedupe_key          TEXT NOT NULL,
    summary             TEXT NOT NULL,
    supporting_signals  TEXT NOT NULL,
    confidence          REAL NOT NULL,
    severity            TEXT NOT NULL,
    actionability       TEXT NOT NULL,
    source              TEXT NOT NULL,
    review_date         TEXT NOT NULL,
    expires_at          TEXT,
    created_at          TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_insights_dedup
ON insights(review_date, insight_type, dedupe_key);
