-- Slice 6c: vault scanner read-side index + vault_synced memory audit event.

CREATE TABLE IF NOT EXISTS vault_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_path TEXT NOT NULL UNIQUE,
    note_type TEXT,
    scope TEXT,
    content_hash TEXT NOT NULL,
    last_scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_vault_index_note_type ON vault_index(note_type);
CREATE INDEX IF NOT EXISTS idx_vault_index_memory_id ON vault_index(memory_id);
CREATE INDEX IF NOT EXISTS idx_vault_index_last_scanned_at ON vault_index(last_scanned_at);

-- DROP IF EXISTS guards against a partial-apply crash. This statement changes the
-- migration checksum. Any DB that already applied 018 before this edit will get a
-- RuntimeError("Migration ... has been modified after application") on next startup.
-- Recovery: DELETE FROM _migrations WHERE name = '018_vault_index.sql'; then restart.
-- That is safe here because 018 is idempotent (CREATE TABLE IF NOT EXISTS, INSERT...SELECT).
DROP TABLE IF EXISTS memory_events_018_new;

CREATE TABLE memory_events_018_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'created',
            'promoted',
            'confirmed',
            'rejected',
            'expired',
            'payload_updated',
            'vault_synced'
        )
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT 'system' CHECK (
        actor IN ('system', 'detector', 'user', 'harness', 'vault_sync')
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO memory_events_018_new (id, memory_id, event_type, payload_json, actor, created_at)
SELECT id, memory_id, event_type, payload_json, actor, created_at
FROM memory_events;

DROP TABLE memory_events;
ALTER TABLE memory_events_018_new RENAME TO memory_events;

CREATE INDEX IF NOT EXISTS idx_memory_events_memory_id ON memory_events (memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_events_type_created
    ON memory_events (event_type, created_at);
