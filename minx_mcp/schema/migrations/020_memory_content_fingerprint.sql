-- Slice 6g: content-based deduplication for memories.
--
-- ADD COLUMN follows the repo convention (see 008_finance_phase2.sql): raw
-- ALTER TABLE, trusting the migration checksum guard in db.py for re-apply
-- safety. SQLite has no ADD COLUMN IF NOT EXISTS.

ALTER TABLE memories ADD COLUMN content_fingerprint TEXT;

-- Partial unique index: only LIVE rows compete for fingerprint exclusivity.
-- Rejected and expired rows retain their fingerprint for audit and for
-- the "re-propose after expiry" reopen path, but they do not block new
-- rows. Rationale mirrors the existing uq_memories_live_triple partial
-- index shipped in migration 015.
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_content_fingerprint_live
    ON memories(content_fingerprint)
    WHERE content_fingerprint IS NOT NULL
      AND status IN ('candidate', 'active');

-- Full (non-partial) index on content_fingerprint. Required consumer:
-- ingest_proposals (§7.2) performs a "has this content been rejected
-- before?" lookup that must see rejected and expired rows, so a
-- partial-live-only index cannot serve it. Non-unique because rejected
-- rows can legitimately share a fingerprint with each other.
CREATE INDEX IF NOT EXISTS idx_memories_content_fingerprint_all
    ON memories(content_fingerprint);
