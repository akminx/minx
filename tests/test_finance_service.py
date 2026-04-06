import pytest

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.finance.service import FinanceService


def test_import_job_is_idempotent_for_same_file(tmp_path):
    db_path = tmp_path / "minx.db"
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    service = FinanceService(db_path, tmp_path)
    first = service.finance_import(str(source), account_name="Robinhood Gold")
    second = service.finance_import(str(source), account_name="Robinhood Gold")
    assert first["job_id"] == second["job_id"]


def test_manual_and_rule_based_categorization_both_work(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")
    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.apply_category_rules()
    tx = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx["category_name"] == "Groceries"
    service.finance_categorize([tx["id"]], "Dining Out")
    changed = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert changed["category_name"] == "Dining Out"


def test_safe_summary_and_sensitive_query_are_separate(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")
    safe = service.safe_finance_summary()
    sensitive = service.sensitive_finance_query(limit=10, session_ref="abc-123")
    assert "transactions" not in safe
    assert sensitive["transactions"][0]["description"] == "H-E-B"


def test_changed_file_at_same_path_creates_new_import(tmp_path):
    db_path = tmp_path / "minx.db"
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
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
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
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
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    aliased_source = tmp_path / "ROBINHOOD_TRANSACTIONS.CSV"
    if not aliased_source.is_file():
        pytest.skip("filesystem is case-sensitive")

    service = FinanceService(tmp_path / "minx.db", tmp_path)

    first = service.finance_import(str(source), account_name="Robinhood Gold")
    second = service.finance_import(str(aliased_source), account_name="Robinhood Gold")

    assert first["job_id"] == second["job_id"]


def test_import_uses_hashed_file_snapshot_for_parse(tmp_path, monkeypatch):
    import hashlib

    from minx_mcp.finance import importers as importers_module
    from minx_mcp.finance import service as service_module

    original_contents = (
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    mutated_contents = (
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-99.99,MUTATED\n"
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
    ):
        path.write_text(mutated_contents)
        return importers_module.parse_source_file(
            path,
            account_name,
            source_kind,
            mapping,
            file_bytes=file_bytes,
            content_hash=content_hash,
        )

    monkeypatch.setattr(service_module, "parse_source_file", mutate_before_parse)

    result = service.finance_import(str(source), account_name="Robinhood Gold")
    batch = service.conn.execute(
        "SELECT raw_fingerprint FROM finance_import_batches WHERE id = ?",
        (result["result"]["batch_id"],),
    ).fetchone()
    transaction = service.conn.execute(
        "SELECT description, amount FROM finance_transactions"
    ).fetchone()

    assert batch["raw_fingerprint"] == hashlib.sha256(original_contents.encode()).hexdigest()
    assert transaction["description"] == "COFFEE"
    assert transaction["amount"] == -12.50


def test_import_returns_running_job_without_reexecuting(tmp_path):
    import hashlib

    from minx_mcp.jobs import mark_running, submit_job

    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
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
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
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


def test_anomalies_flag_large_uncategorized_transactions(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-02,Unknown Merchant,Withdrawal,-500.00\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    service.finance_import(str(source), account_name="DCU")
    anomalies = service.finance_anomalies()
    assert anomalies["items"][0]["kind"] == "large_uncategorized"


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


def test_close_reopens_connection_on_next_use(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    first_conn = service.conn
    service.close()
    second_conn = service.conn

    assert first_conn is not second_conn


def test_import_batch_stores_content_hash_as_raw_fingerprint(tmp_path):
    source = tmp_path / "robinhood_transactions.csv"
    contents = (
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
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


def test_service_import_rejects_paths_outside_allowed_import_root(tmp_path):
    import_root = tmp_path / "staging"
    import_root.mkdir()
    outside_source = tmp_path / "outside.csv"
    outside_source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=import_root)

    with pytest.raises(InvalidInputError, match="source_ref must be inside the allowed import root"):
        service.finance_import(str(outside_source), account_name="DCU")


def test_service_get_job_raises_not_found_for_missing_job(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    with pytest.raises(NotFoundError, match="Unknown finance job id: missing-job"):
        service.get_job("missing-job")
