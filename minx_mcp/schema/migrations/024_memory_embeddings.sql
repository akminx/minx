-- Slice 6l: queued memory embeddings.

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    content_fingerprint TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding_json TEXT NOT NULL,
    cost_microusd INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (dimensions > 0),
    CHECK (cost_microusd >= 0)
);

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model
    ON memory_embeddings(provider, model);

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_fingerprint
    ON memory_embeddings(content_fingerprint);
