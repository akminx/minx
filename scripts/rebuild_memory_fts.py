from __future__ import annotations

import argparse
from pathlib import Path

from minx_mcp.config import get_settings
from minx_mcp.db import get_connection


def rebuild_memory_fts(db_path: Path) -> int:
    conn = get_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM memory_fts")
        conn.execute(
            """
            INSERT INTO memory_fts(rowid, memory_type, scope, subject, payload_text, source, reason)
            SELECT
                id,
                memory_type,
                scope,
                subject,
                CASE
                    WHEN json_valid(payload_json) THEN
                        COALESCE(json_extract(payload_json, '$.value'), '') || ' ' ||
                        COALESCE(json_extract(payload_json, '$.note'), '') || ' ' ||
                        COALESCE(json_extract(payload_json, '$.signal'), '') || ' ' ||
                        COALESCE(json_extract(payload_json, '$.limit_value'), '') || ' ' ||
                        COALESCE(json_extract(payload_json, '$.aliases'), '')
                    ELSE ''
                END,
                source,
                reason
            FROM memories
            """
        )
        count = int(conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0])
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
    else:
        return count
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild the memory FTS5 index.")
    parser.add_argument("db_path", nargs="?", type=Path, default=get_settings().db_path)
    args = parser.parse_args(argv)

    count = rebuild_memory_fts(args.db_path)
    print(f"Indexed {count} memory rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
