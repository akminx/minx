DROP VIEW IF EXISTS v_finance_monthly_spend;

CREATE TABLE finance_transactions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES finance_accounts(id),
    batch_id INTEGER NOT NULL REFERENCES finance_import_batches(id),
    posted_at TEXT NOT NULL,
    description TEXT NOT NULL,
    merchant TEXT,
    amount_cents INTEGER NOT NULL,
    category_id INTEGER REFERENCES finance_categories(id),
    category_source TEXT NOT NULL DEFAULT 'uncategorized',
    external_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO finance_transactions_new (
    id,
    account_id,
    batch_id,
    posted_at,
    description,
    merchant,
    amount_cents,
    category_id,
    category_source,
    external_id,
    notes,
    created_at
)
SELECT
    id,
    account_id,
    batch_id,
    posted_at,
    description,
    merchant,
    CAST(ROUND(amount * 100, 0) AS INTEGER),
    category_id,
    category_source,
    external_id,
    notes,
    created_at
FROM finance_transactions;

DROP TABLE finance_transactions;
ALTER TABLE finance_transactions_new RENAME TO finance_transactions;

CREATE INDEX IF NOT EXISTS idx_finance_transactions_posted_at
ON finance_transactions(posted_at);

CREATE INDEX IF NOT EXISTS idx_finance_transactions_category
ON finance_transactions(category_id);

CREATE VIEW IF NOT EXISTS v_finance_monthly_spend AS
SELECT
    substr(posted_at, 1, 7) AS month,
    COALESCE(c.name, 'Uncategorized') AS category_name,
    SUM(t.amount_cents) / 100.0 AS total_amount
FROM finance_transactions t
LEFT JOIN finance_categories c ON c.id = t.category_id
GROUP BY substr(posted_at, 1, 7), COALESCE(c.name, 'Uncategorized');
