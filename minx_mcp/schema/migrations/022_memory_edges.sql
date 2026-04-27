-- Slice 6j: typed relationships between memory rows.

CREATE TABLE IF NOT EXISTS memory_edges (
    id INTEGER PRIMARY KEY,
    source_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    target_memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    predicate TEXT NOT NULL CHECK (predicate IN ('supersedes', 'contradicts', 'cites')),
    relation_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    actor TEXT NOT NULL DEFAULT 'system',
    CHECK (source_memory_id != target_memory_id),
    UNIQUE (source_memory_id, target_memory_id, predicate)
);

CREATE INDEX IF NOT EXISTS idx_memory_edges_source
    ON memory_edges(source_memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_edges_target
    ON memory_edges(target_memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_edges_predicate
    ON memory_edges(predicate);
