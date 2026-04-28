-- Slice 6 memory capture: include captured text fields in future FTS trigger writes.
--
-- Existing FTS rows should be refreshed with scripts/rebuild_memory_fts.py
-- after this migration if the database already contains captured_thought rows.

DROP TRIGGER IF EXISTS memories_ai_fts;
DROP TRIGGER IF EXISTS memories_au_fts;
DROP TRIGGER IF EXISTS memories_ad_fts;

CREATE TRIGGER IF NOT EXISTS memories_ai_fts AFTER INSERT ON memories BEGIN
    INSERT INTO memory_fts(rowid, memory_type, scope, subject, payload_text, source, reason)
    VALUES (
        new.id,
        new.memory_type,
        new.scope,
        new.subject,
        CASE
            WHEN json_valid(new.payload_json) THEN
                COALESCE(json_extract(new.payload_json, '$.value'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.note'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.signal'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.limit_value'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.aliases'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.text'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.capture_type'), '')
            ELSE ''
        END,
        new.source,
        new.reason
    );
END;

CREATE TRIGGER IF NOT EXISTS memories_au_fts AFTER UPDATE OF memory_type, scope, subject, payload_json, source, reason ON memories BEGIN
    DELETE FROM memory_fts WHERE rowid = old.id;
    INSERT INTO memory_fts(rowid, memory_type, scope, subject, payload_text, source, reason)
    VALUES (
        new.id,
        new.memory_type,
        new.scope,
        new.subject,
        CASE
            WHEN json_valid(new.payload_json) THEN
                COALESCE(json_extract(new.payload_json, '$.value'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.note'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.signal'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.limit_value'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.aliases'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.text'), '') || ' ' ||
                COALESCE(json_extract(new.payload_json, '$.capture_type'), '')
            ELSE ''
        END,
        new.source,
        new.reason
    );
END;

CREATE TRIGGER IF NOT EXISTS memories_ad_fts AFTER DELETE ON memories BEGIN
    DELETE FROM memory_fts WHERE rowid = old.id;
END;
