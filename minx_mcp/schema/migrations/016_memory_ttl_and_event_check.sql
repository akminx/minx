-- 016_memory_ttl_and_event_check.sql
-- Narrow memory_events.event_type CHECK to types actually emitted by MemoryService.
-- vault_synced will be re-added in Slice 6c when the vault-sync path lands.
--
-- Rows with event_type not in the new allowlist (e.g. vault_synced, reopened) are
-- omitted by the INSERT … SELECT and therefore dropped if they ever existed; no
-- code path emits those types today, so this should be a no-op for real data.
--
-- Runs inside apply_migrations' outer transaction (no nested BEGIN/COMMIT).

CREATE TABLE memory_events_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'created',
            'promoted',
            'confirmed',
            'rejected',
            'expired',
            'payload_updated'
        )
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT 'system' CHECK (
        actor IN ('system', 'detector', 'user', 'harness', 'vault_sync')
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO memory_events_new (id, memory_id, event_type, payload_json, actor, created_at)
SELECT id, memory_id, event_type, payload_json, actor, created_at
FROM memory_events
WHERE event_type IN (
    'created',
    'promoted',
    'confirmed',
    'rejected',
    'expired',
    'payload_updated'
);

DROP TABLE memory_events;
ALTER TABLE memory_events_new RENAME TO memory_events;

CREATE INDEX IF NOT EXISTS idx_memory_events_memory_id ON memory_events (memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_events_type_created
    ON memory_events (event_type, created_at);
