CREATE TABLE IF NOT EXISTS finance_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    account_type TEXT NOT NULL,
    import_profile TEXT,
    last_imported_at TEXT
);

CREATE TABLE IF NOT EXISTS finance_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES finance_categories(id)
);

CREATE TABLE IF NOT EXISTS finance_category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES finance_categories(id),
    match_kind TEXT NOT NULL,
    pattern TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS finance_import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES finance_accounts(id),
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    raw_fingerprint TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    inserted_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS finance_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES finance_accounts(id),
    batch_id INTEGER NOT NULL REFERENCES finance_import_batches(id),
    posted_at TEXT NOT NULL,
    description TEXT NOT NULL,
    merchant TEXT,
    amount REAL NOT NULL,
    category_id INTEGER REFERENCES finance_categories(id),
    category_source TEXT NOT NULL DEFAULT 'uncategorized',
    external_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS finance_transaction_dedupe (
    fingerprint TEXT PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES finance_transactions(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS finance_report_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_kind TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    vault_path TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_finance_transactions_posted_at
ON finance_transactions(posted_at);

CREATE INDEX IF NOT EXISTS idx_finance_transactions_category
ON finance_transactions(category_id);

INSERT OR IGNORE INTO finance_accounts (name, account_type, import_profile) VALUES
    ('DCU', 'bank', 'dcu'),
    ('Discover', 'credit', 'discover'),
    ('Robinhood Gold', 'credit', 'robinhood_gold');

INSERT OR IGNORE INTO finance_categories (name) VALUES
    ('Uncategorized'),
    ('Groceries'),
    ('Dining Out'),
    ('Income'),
    ('Subscriptions'),
    ('Shopping'),
    ('Transportation');

