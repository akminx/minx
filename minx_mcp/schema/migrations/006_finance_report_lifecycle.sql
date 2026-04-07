CREATE TABLE finance_report_runs_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_kind TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    vault_path TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO finance_report_runs_new (
    id,
    report_kind,
    period_start,
    period_end,
    vault_path,
    summary_json,
    status,
    error_message,
    created_at,
    updated_at
)
SELECT
    r.id,
    r.report_kind,
    r.period_start,
    r.period_end,
    r.vault_path,
    r.summary_json,
    'completed',
    NULL,
    r.created_at,
    r.created_at
FROM finance_report_runs AS r
INNER JOIN (
    SELECT MAX(id) AS keep_id
    FROM finance_report_runs
    GROUP BY report_kind, period_start, period_end
) AS latest
    ON latest.keep_id = r.id;

DROP TABLE finance_report_runs;

ALTER TABLE finance_report_runs_new RENAME TO finance_report_runs;

CREATE UNIQUE INDEX idx_finance_report_runs_identity
ON finance_report_runs(report_kind, period_start, period_end);
