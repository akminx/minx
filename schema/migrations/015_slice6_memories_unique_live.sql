-- Slice 6 post-6a hardening: at most one live (candidate/active) row per
-- (memory_type, scope, subject). Rejected and expired rows are treated as
-- history and are excluded from the constraint, which is why this is a
-- partial unique index rather than a plain UNIQUE() on the triple.
--
-- Application logic in ``MemoryService.ingest_proposals`` already enforces
-- this invariant. The index makes it durable against concurrent writers
-- and guards against future callers that bypass ``ingest_proposals``.

CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_live_triple
    ON memories(memory_type, scope, subject)
    WHERE status IN ('candidate', 'active');
