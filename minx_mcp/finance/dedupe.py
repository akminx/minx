from __future__ import annotations

import hashlib
import sqlite3

from minx_mcp.finance.import_models import ParsedTransaction
from minx_mcp.finance.normalization import normalize_merchant


def fingerprint_transaction(account_id: int, transaction: ParsedTransaction) -> str:
    if transaction.external_id:
        dedupe_key = str(transaction.external_id)
    else:
        normalized = normalize_merchant(transaction.merchant)
        dedupe_key = (
            normalized.casefold()
            if normalized
            else (transaction.description or "").strip().casefold()
        )
    raw = "|".join(
        [
            str(account_id),
            str(transaction.posted_at),
            str(transaction.description),
            str(int(transaction.amount_cents)),
            dedupe_key,
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def rebuild_dedupe_fingerprints(conn: sqlite3.Connection) -> int:
    """Recompute every ``finance_transaction_dedupe`` row from ``finance_transactions``.

    The fingerprint algorithm changed to include merchant identity for rows whose
    ``external_id`` is empty (previously such rows shared a constant empty slot,
    silently collapsing legitimate same-day / same-description / same-amount rows
    that differed only by merchant). Dedupe rows stored under the old algorithm no
    longer match the new hashes, so re-importing the exact same source file after
    upgrade would produce duplicates. This helper clears the dedupe table and
    re-inserts one row per persisted transaction using the current algorithm,
    wrapped in a savepoint so an interruption leaves the connection's state
    consistent (all-or-nothing on this connection). It is **not** safe to run
    concurrently with an import or other writer on the same database — run it
    offline, once per upgrade. Returns the number of transactions processed
    (note: collisions from ``INSERT OR IGNORE`` could make the stored row count
    smaller than this counter; the caller should also compare ``COUNT(*)``).
    Safe to run multiple times.
    """
    conn.execute("SAVEPOINT rebuild_dedupe")
    try:
        conn.execute("DELETE FROM finance_transaction_dedupe")
        rows = conn.execute(
            """
            SELECT id, account_id, posted_at, description, raw_merchant,
                   amount_cents, external_id
            FROM finance_transactions
            ORDER BY id
            """
        ).fetchall()
        written = 0
        for row in rows:
            txn = ParsedTransaction(
                posted_at=str(row["posted_at"]),
                description=str(row["description"]),
                amount_cents=int(row["amount_cents"]),
                merchant=(
                    str(row["raw_merchant"]) if row["raw_merchant"] is not None else None
                ),
                category_hint=None,
                external_id=(
                    str(row["external_id"]) if row["external_id"] is not None else None
                ),
            )
            fingerprint = fingerprint_transaction(int(row["account_id"]), txn)
            conn.execute(
                "INSERT OR IGNORE INTO finance_transaction_dedupe "
                "(fingerprint, transaction_id) VALUES (?, ?)",
                (fingerprint, int(row["id"])),
            )
            written += 1
        conn.execute("RELEASE SAVEPOINT rebuild_dedupe")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT rebuild_dedupe")
        conn.execute("RELEASE SAVEPOINT rebuild_dedupe")
        raise
    return written
