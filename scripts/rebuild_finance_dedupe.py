"""One-shot maintenance script to rebuild ``finance_transaction_dedupe``.

Run once after upgrading past the hardening wave that changed the dedupe
fingerprint to include merchant identity for rows with empty ``external_id``.
Without this rebuild, fingerprints stored under the old algorithm do not match
the new hashes, and re-importing the same source file can produce duplicate
transactions.

Usage:
    python -m scripts.rebuild_finance_dedupe [DB_PATH]

``DB_PATH`` defaults to ``MINX_DB_PATH`` from the environment (or the default
settings path if unset). The operation is idempotent and wrapped in a
savepoint, so interruption leaves the database in a consistent state.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from minx_mcp.config import get_settings
from minx_mcp.db import get_connection
from minx_mcp.finance.dedupe import rebuild_dedupe_fingerprints


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "db_path",
        nargs="?",
        type=Path,
        help="SQLite database path (defaults to MINX_DB_PATH / settings default).",
    )
    args = parser.parse_args(argv)

    db_path: Path = args.db_path if args.db_path else get_settings().db_path
    conn = get_connection(db_path)
    try:
        before = conn.execute(
            "SELECT COUNT(*) AS c FROM finance_transaction_dedupe"
        ).fetchone()["c"]
        written = rebuild_dedupe_fingerprints(conn)
        conn.commit()
        after = conn.execute(
            "SELECT COUNT(*) AS c FROM finance_transaction_dedupe"
        ).fetchone()["c"]
    finally:
        conn.close()

    print(
        f"finance_transaction_dedupe rebuilt: {before} old rows replaced, "
        f"{written} transactions processed, {after} fingerprints now stored."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
