from pathlib import Path

from minx_mcp.db import apply_migrations, get_connection
from minx_mcp.finance.dedupe import fingerprint_transaction, rebuild_dedupe_fingerprints
from minx_mcp.finance.import_models import ParsedTransaction


def test_dedupe_fingerprint_separates_same_amount_different_merchants() -> None:
    a = ParsedTransaction(
        posted_at="2026-03-01",
        description="COFFEE",
        merchant="CAFE A",
        amount_cents=-500,
        category_hint=None,
        external_id=None,
    )
    b = ParsedTransaction(
        posted_at="2026-03-01",
        description="COFFEE",
        merchant="CAFE B",
        amount_cents=-500,
        category_hint=None,
        external_id=None,
    )
    assert fingerprint_transaction(1, a) != fingerprint_transaction(1, b)


def test_dedupe_fingerprint_reuses_external_id_when_present() -> None:
    a = ParsedTransaction(
        posted_at="2026-03-01",
        description="COFFEE",
        merchant="CAFE A",
        amount_cents=-500,
        category_hint=None,
        external_id="bank-ref-1",
    )
    b = ParsedTransaction(
        posted_at="2026-03-01",
        description="COFFEE",
        merchant="CAFE B",
        amount_cents=-500,
        category_hint=None,
        external_id="bank-ref-1",
    )
    assert fingerprint_transaction(1, a) == fingerprint_transaction(1, b)


def test_rebuild_dedupe_fingerprints_migrates_old_rows(tmp_path: Path) -> None:
    """Simulate the post-upgrade migration path.

    Pre-change, dedupe rows were stored with a fingerprint that ignored merchant.
    After upgrading, those fingerprints no longer match fresh hashes. The rebuild
    utility must: (1) clear the old rows, (2) re-insert one row per transaction
    using the current fingerprint algorithm, so a subsequent re-import of the
    same source sees those rows as duplicates and skips them.
    """
    db_path = tmp_path / "rebuild.db"
    conn = get_connection(db_path)
    try:
        apply_migrations(conn)
        cursor = conn.execute(
            "INSERT INTO finance_import_batches "
            "(account_id, source_type, source_ref, raw_fingerprint) VALUES (?, ?, ?, ?)",
            (1, "csv", "legacy.csv", "sha-legacy"),
        )
        batch_id = cursor.lastrowid
        assert batch_id is not None
        for raw_merchant in ("CAFE A", "CAFE B"):
            conn.execute(
                """
                INSERT INTO finance_transactions
                    (account_id, batch_id, posted_at, description, merchant,
                     raw_merchant, amount_cents, category_id, category_source,
                     external_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, batch_id, "2026-03-01", "COFFEE", raw_merchant, raw_merchant,
                 -500, 1, "uncategorized", None),
            )

        legacy_fingerprint = "|".join(
            ["1", "2026-03-01", "COFFEE", "-500", ""]
        )
        import hashlib
        legacy_hash = hashlib.sha256(legacy_fingerprint.encode()).hexdigest()
        tx_ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM finance_transactions ORDER BY id"
            ).fetchall()
        ]
        conn.execute(
            "INSERT INTO finance_transaction_dedupe (fingerprint, transaction_id) VALUES (?, ?)",
            (legacy_hash, tx_ids[0]),
        )
        conn.commit()

        written = rebuild_dedupe_fingerprints(conn)
        conn.commit()

        assert written == 2
        stored = [
            row["fingerprint"]
            for row in conn.execute(
                "SELECT fingerprint FROM finance_transaction_dedupe ORDER BY fingerprint"
            ).fetchall()
        ]
        assert legacy_hash not in stored
        txn_a = ParsedTransaction(
            posted_at="2026-03-01", description="COFFEE", merchant="CAFE A",
            amount_cents=-500, category_hint=None, external_id=None,
        )
        txn_b = ParsedTransaction(
            posted_at="2026-03-01", description="COFFEE", merchant="CAFE B",
            amount_cents=-500, category_hint=None, external_id=None,
        )
        expected = sorted(
            [fingerprint_transaction(1, txn_a), fingerprint_transaction(1, txn_b)]
        )
        assert stored == expected

        re_written = rebuild_dedupe_fingerprints(conn)
        conn.commit()
        assert re_written == 2
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM finance_transaction_dedupe"
        ).fetchone()["c"] == 2
    finally:
        conn.close()
