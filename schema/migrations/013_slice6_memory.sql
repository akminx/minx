-- Slice 6a: durable memory tables (machine-queryable tier)

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type TEXT NOT NULL,
    scope TEXT NOT NULL,
    subject TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL DEFAULT 'candidate' CHECK(
        status IN ('candidate', 'active', 'rejected', 'expired')
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_confirmed_at TEXT,
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_memories_type_scope_subject
    ON memories(memory_type, scope, subject);

CREATE INDEX IF NOT EXISTS idx_memories_status_memory_type
    ON memories(status, memory_type);

CREATE TABLE IF NOT EXISTS memory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK(
        event_type IN (
            'created',
            'promoted',
            'confirmed',
            'rejected',
            'expired',
            'payload_updated',
            'vault_synced',
            'reopened'
        )
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT 'system' CHECK(
        actor IN ('system', 'detector', 'user', 'harness', 'vault_sync')
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memory_events_memory_id ON memory_events(memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_events_type_created
    ON memory_events(event_type, created_at);
