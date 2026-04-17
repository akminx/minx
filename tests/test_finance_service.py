from pathlib import Path

import pytest

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.finance.service import FinanceService
from minx_mcp.preferences import set_preference


def test_import_job_is_idempotent_for_same_file(tmp_path):
    db_path = tmp_path / "minx.db"
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    service = FinanceService(db_path, tmp_path)
    first = service.finance_import(str(source), account_name="Robinhood Gold")
    second = service.finance_import(str(source), account_name="Robinhood Gold")
    assert first["job_id"] == second["job_id"]


def test_import_uses_content_detection_when_filename_is_unhelpful(tmp_path):
    source = tmp_path / "statement.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    result = service.finance_import(str(source), account_name="DCU")

    assert result["result"]["inserted"] == 1


def test_manual_and_rule_based_categorization_both_work(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")
    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.apply_category_rules()
    tx = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx["category_name"] == "Groceries"
    service.finance_categorize([tx["id"]], "Dining Out")
    changed = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert changed["category_name"] == "Dining Out"


def test_apply_category_rules_uses_normalized_merchant_matching(tmp_path):
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,SQ *JOES CAFE 1234\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="Robinhood Gold")
    service.add_category_rule("Dining Out", "merchant_contains", "JOES CAFE")

    service.apply_category_rules()

    tx = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx["merchant"] == "Joe's Cafe"
    assert tx["category_name"] == "Dining Out"


def test_safe_summary_and_sensitive_query_are_separate(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")
    safe = service.safe_finance_summary()
    sensitive = service.sensitive_finance_query(limit=10, session_ref="abc-123")
    assert "transactions" not in safe
    assert sensitive["transactions"][0]["description"] == "H-E-B"


def test_sensitive_query_supports_optional_filters(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    groceries_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Groceries'"
    ).fetchone()["id"]
    dining_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Dining Out'"
    ).fetchone()["id"]
    dcu_id = service.conn.execute("SELECT id FROM finance_accounts WHERE name = 'DCU'").fetchone()[
        "id"
    ]
    discover_id = service.conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'Discover'"
    ).fetchone()["id"]
    service.conn.executemany(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (?, ?, 'csv', ?, ?)
        """,
        [
            (1, dcu_id, "dcu.csv", "fp-1"),
            (2, discover_id, "discover.csv", "fp-2"),
        ],
    )
    service.conn.executemany(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (dcu_id, 1, "2026-03-02", "H-E-B Grocery", "H-E-B", -4520, groceries_id, "manual"),
            (discover_id, 2, "2026-03-06", "Coffee Shop", "Cafe", -1200, dining_id, "manual"),
            (dcu_id, 1, "2026-04-01", "April Rent", "Landlord", -50000, groceries_id, "manual"),
        ],
    )
    service.conn.commit()

    result = service.sensitive_finance_query(
        limit=10,
        start_date="2026-03-01",
        end_date="2026-03-31",
        category_name="Groceries",
        merchant="H-E-B",
        account_name="DCU",
        description_contains="Grocery",
    )

    assert [txn["description"] for txn in result["transactions"]] == ["H-E-B Grocery"]


def test_sensitive_query_hides_internal_amount_cents_field(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")

    transaction = service.sensitive_finance_query(limit=1)["transactions"][0]

    assert transaction["amount"] == -45.2
    assert "amount_cents" not in transaction


def test_finance_import_uses_category_hint_to_match_existing_categories(tmp_path):
    source = tmp_path / "transactions.csv"
    source.write_text("posted,description,amount,category\n2026-03-02,Coffee,-12.50,Dining Out\n")
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    service.finance_import(
        str(source),
        account_name="DCU",
        source_kind="generic_csv",
        mapping={
            "date_column": "posted",
            "amount_column": "amount",
            "description_column": "description",
            "date_format": "%Y-%m-%d",
            "category_hint_column": "category",
        },
    )

    transaction = service.sensitive_finance_query(limit=1)["transactions"][0]

    assert transaction["category_name"] == "Dining Out"


def test_changed_file_at_same_path_creates_new_import(tmp_path):
    db_path = tmp_path / "minx.db"
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    service = FinanceService(db_path, tmp_path)
    first = service.finance_import(str(source), account_name="Robinhood Gold")
    assert first["result"]["inserted"] == 1

    # Modify the file at the same path
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
        "2026-03-02,10:00,Alex,1234,-8.00,LUNCH\n"
    )
    second = service.finance_import(str(source), account_name="Robinhood Gold")
    assert second["job_id"] != first["job_id"]
    assert second["result"]["inserted"] == 1  # COFFEE deduped, LUNCH new
    assert second["result"]["skipped"] == 1


def test_import_job_is_idempotent_across_path_aliases(tmp_path):
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    (tmp_path / "alias").mkdir()
    aliased_source = tmp_path / "alias" / ".." / "robinhood_transactions.csv"
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    first = service.finance_import(str(source), account_name="Robinhood Gold")
    second = service.finance_import(str(aliased_source), account_name="Robinhood Gold")

    assert first["job_id"] == second["job_id"]


def test_import_job_is_idempotent_across_case_only_aliases(tmp_path):
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    aliased_source = tmp_path / "ROBINHOOD_TRANSACTIONS.CSV"
    if not aliased_source.is_file():
        pytest.skip("filesystem is case-sensitive")

    service = FinanceService(tmp_path / "minx.db", tmp_path)

    first = service.finance_import(str(source), account_name="Robinhood Gold")
    second = service.finance_import(str(aliased_source), account_name="Robinhood Gold")

    assert first["job_id"] == second["job_id"]


def test_finance_import_normal_path_does_not_read_source_via_read_bytes(tmp_path, monkeypatch):
    """Import should stream the source file, not load it entirely via Path.read_bytes()."""
    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(self: Path) -> bytes:
        try:
            if self.resolve() == (tmp_path / "robinhood_transactions.csv").resolve():
                raise AssertionError(
                    "finance import must not use Path.read_bytes() on the source file"
                )
        except OSError:
            pass
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    result = service.finance_import(str(source), account_name="Robinhood Gold")
    assert result["result"]["inserted"] == 1


def test_import_uses_hashed_file_snapshot_for_parse(tmp_path, monkeypatch):
    import hashlib

    from minx_mcp.finance import import_workflow as import_workflow_module
    from minx_mcp.finance import importers as importers_module

    original_contents = (
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    mutated_contents = (
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-99.99,MUTATED\n"
    )
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(original_contents)
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    def mutate_before_parse(
        path,
        account_name,
        source_kind=None,
        mapping=None,
        *,
        file_bytes=None,
        content_hash=None,
        snapshot_path=None,
    ):
        path.write_text(mutated_contents)
        return importers_module.parse_source_file(
            path,
            account_name,
            source_kind,
            mapping,
            file_bytes=file_bytes,
            content_hash=content_hash,
            snapshot_path=snapshot_path,
        )

    monkeypatch.setattr(import_workflow_module, "parse_source_file", mutate_before_parse)

    result = service.finance_import(str(source), account_name="Robinhood Gold")
    batch = service.conn.execute(
        "SELECT raw_fingerprint FROM finance_import_batches WHERE id = ?",
        (result["result"]["batch_id"],),
    ).fetchone()
    transaction = service.conn.execute(
        "SELECT description, amount_cents FROM finance_transactions"
    ).fetchone()

    assert batch["raw_fingerprint"] == hashlib.sha256(original_contents.encode()).hexdigest()
    assert transaction["description"] == "COFFEE"
    assert transaction["amount_cents"] == -1250


def test_import_returns_running_job_without_reexecuting(tmp_path):
    import hashlib

    from minx_mcp.jobs import mark_running, submit_job

    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    content_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    idempotency_key = hashlib.sha256(
        f"Robinhood Gold|{source.resolve()}|{content_hash}".encode()
    ).hexdigest()
    job = submit_job(
        service.conn,
        "finance_import",
        "system",
        str(source.resolve()),
        idempotency_key,
    )
    mark_running(service.conn, str(job["id"]))

    result = service.finance_import(str(source), account_name="Robinhood Gold")
    transaction_count = service.conn.execute(
        "SELECT COUNT(*) AS count FROM finance_transactions"
    ).fetchone()["count"]

    assert result == {"job_id": job["id"], "status": "running", "result": None}
    assert transaction_count == 0


def test_manual_categorization_survives_rule_reapplication(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")

    # Apply rule first
    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.apply_category_rules()
    tx = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx["category_name"] == "Groceries"

    # Manually override
    service.finance_categorize([tx["id"]], "Dining Out")

    # Re-apply rules — manual categorization must survive
    service.apply_category_rules()
    tx_after = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx_after["category_name"] == "Dining Out"


def test_apply_category_rules_can_scope_updates_to_a_batch(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    groceries_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Groceries'"
    ).fetchone()["id"]
    uncategorized_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Uncategorized'"
    ).fetchone()["id"]
    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.conn.executemany(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (?, 1, 'csv', ?, ?)
        """,
        [
            (1, "old.csv", "fp-old"),
            (2, "new.csv", "fp-new"),
        ],
    )
    service.conn.executemany(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, "2026-03-01", "old heb", "H-E-B", -1000, uncategorized_id, "uncategorized"),
            (1, 2, "2026-03-02", "new heb", "H-E-B", -2000, uncategorized_id, "uncategorized"),
        ],
    )
    service.conn.commit()

    service.apply_category_rules(batch_id=2)

    rows = service.conn.execute(
        """
        SELECT description, category_id
        FROM finance_transactions
        ORDER BY id
        """
    ).fetchall()

    assert rows[0]["description"] == "old heb"
    assert rows[0]["category_id"] == uncategorized_id
    assert rows[1]["description"] == "new heb"
    assert rows[1]["category_id"] == groceries_id


def test_anomalies_flag_large_uncategorized_transactions(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,Unknown Merchant,Withdrawal,-500.00\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")
    anomalies = service.finance_anomalies()
    assert anomalies["items"][0]["kind"] == "large_uncategorized"


def test_finance_anomalies_reads_threshold_from_preferences(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    batch_id = 1
    account_id = service.conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]
    uncategorized_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Uncategorized'"
    ).fetchone()["id"]
    service.conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (?, ?, 'csv', 'seed.csv', 'fp')
        """,
        (batch_id, account_id),
    )
    service.conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, ?, '2026-03-02', 'Small Merchant', 'Small Merchant', ?, ?, 'uncategorized')
        """,
        (account_id, batch_id, -10_000, uncategorized_id),
    )
    service.conn.commit()
    set_preference(service.conn, "finance", "anomaly_threshold_cents", -9_000)

    anomalies = service.finance_anomalies()

    assert [item["transaction_id"] for item in anomalies["items"]] == [1]


def test_merchant_rule_treats_like_wildcards_as_literal_text(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-02,50%_OFF MARKET,Withdrawal,-45.20\n"
        "2026-03-03,500XOFF MARKET,Withdrawal,-30.00\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")

    service.add_category_rule("Groceries", "merchant_contains", "50%_OFF")
    service.apply_category_rules()

    transactions = service.sensitive_finance_query(limit=10)["transactions"]
    by_description = {txn["description"]: txn["category_name"] for txn in transactions}

    assert by_description["50%_OFF MARKET"] == "Groceries"
    assert by_description["500XOFF MARKET"] == "Uncategorized"


def test_apply_category_rules_clears_stale_rule_categories_when_rules_change(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")

    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.apply_category_rules()
    initial = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert initial["category_name"] == "Groceries"

    service.conn.execute("DELETE FROM finance_category_rules")
    service.conn.commit()

    service.apply_category_rules()

    updated = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert updated["category_name"] == "Uncategorized"


def test_close_reopens_connection_on_next_use(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    first_conn = service.conn
    service.close()
    second_conn = service.conn

    assert first_conn is not second_conn


def test_import_batch_stores_content_hash_as_raw_fingerprint(tmp_path):
    source = tmp_path / "robinhood_transactions.csv"
    contents = (
        "Date,Time,Cardholder,Card,Amount,Description\n2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    source.write_text(contents)
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    result = service.finance_import(str(source), account_name="Robinhood Gold")
    row = service.conn.execute(
        "SELECT raw_fingerprint FROM finance_import_batches WHERE id = ?",
        (result["result"]["batch_id"],),
    ).fetchone()

    import hashlib

    assert row["raw_fingerprint"] == hashlib.sha256(contents.encode()).hexdigest()


def test_service_categorize_rejects_empty_transaction_ids(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    with pytest.raises(InvalidInputError, match="transaction_ids must be a non-empty list"):
        service.finance_categorize([], "Groceries")


def test_service_sensitive_query_rejects_invalid_limits(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    with pytest.raises(InvalidInputError, match="limit must be between 1 and 500"):
        service.sensitive_finance_query(limit=0)

    with pytest.raises(InvalidInputError, match="limit must be between 1 and 500"):
        service.sensitive_finance_query(limit=-1)


def test_sensitive_query_does_not_commit_ambient_transaction(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    service.conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json, updated_at)
        VALUES ('x', 'y', '1', datetime('now'))
        """
    )
    assert service.conn.in_transaction is True

    service.sensitive_finance_query(limit=1)
    service.conn.rollback()

    row = service.conn.execute(
        "SELECT value_json FROM preferences WHERE domain = 'x' AND key = 'y'"
    ).fetchone()
    assert row is None


def test_service_import_rejects_paths_outside_allowed_import_root(tmp_path):
    import_root = tmp_path / "staging"
    import_root.mkdir()
    outside_source = tmp_path / "outside.csv"
    outside_source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=import_root)

    with pytest.raises(
        InvalidInputError, match="source_ref must be inside the allowed import root"
    ):
        service.finance_import(str(outside_source), account_name="DCU")


def test_service_import_rejects_unsupported_explicit_source_kind_before_job_creation(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    with pytest.raises(InvalidInputError, match="Unsupported finance source kind: not_a_real_kind"):
        service.finance_import(
            str(source),
            account_name="DCU",
            source_kind="not_a_real_kind",
        )

    job_count = service.conn.execute("SELECT COUNT(*) AS count FROM jobs").fetchone()["count"]
    batch_count = service.conn.execute(
        "SELECT COUNT(*) AS count FROM finance_import_batches"
    ).fetchone()["count"]

    assert job_count == 0
    assert batch_count == 0


def test_service_get_job_raises_not_found_for_missing_job(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    with pytest.raises(NotFoundError, match="Unknown finance job id: missing-job"):
        service.get_job("missing-job")


def test_finance_import_stores_amount_cents(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-28,HEB,Withdrawal,-12.50\n"
    )

    service.finance_import(str(source), "DCU", source_kind="dcu_csv")

    transaction = service.conn.execute(
        "SELECT description, amount_cents FROM finance_transactions"
    ).fetchone()
    assert transaction["description"] == "HEB"
    assert transaction["amount_cents"] == -1250


def test_safe_finance_summary_returns_dollars_from_cents_storage(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'fp')
        """
    )
    service.conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (1, 1, '2026-03-28', 'HEB', 'HEB', -1250, 1, 'manual')
        """
    )
    service.conn.commit()

    summary = service.safe_finance_summary()

    assert summary["net_total"] == -12.5


def test_import_persists_raw_and_canonical_merchant(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-02,SQ *JOES CAFE 1234,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    service.finance_import(str(source), account_name="DCU")

    tx = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx["merchant"] == "Joe's Cafe"
    assert tx["raw_merchant"] == "SQ *JOES CAFE 1234"
