**Status: Completed (historical).** This plan was executed in an earlier slice. The codebase has since evolved — see [HANDOFF.md](/Users/akmini/Documents/minx-mcp/HANDOFF.md) for current state.

# Finance Cents Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate finance money storage and arithmetic from `REAL` dollars to integer cents while keeping MCP responses, reports, and compatibility-facing views dollar-formatted.

**Architecture:** Add one shared `money.py` module for exact parsing and formatting, migrate SQLite storage to `amount_cents INTEGER`, then update parsers, service logic, dedupe, analytics, and reports to use cents internally. Legacy float handling is limited to the one-time SQL migration path; runtime code stops accepting floats and existing tests move to cents or source-text inputs.

**Tech Stack:** Python 3.12, SQLite, pytest, FastMCP, Decimal, SQL migrations

---

## File Structure

**Create:**
- `/Users/akmini/Documents/minx-mcp/minx_mcp/money.py`
- `/Users/akmini/Documents/minx-mcp/tests/test_money.py`
- `/Users/akmini/Documents/minx-mcp/schema/migrations/004_finance_amount_cents.sql`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/migrations/004_finance_amount_cents.sql`

**Modify:**
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/dcu.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/discover.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/generic_csv.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/robinhood_gold.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/dedupe.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/reports.py`
- `/Users/akmini/Documents/minx-mcp/minx_mcp/db.py`
- `/Users/akmini/Documents/minx-mcp/tests/test_db.py`
- `/Users/akmini/Documents/minx-mcp/tests/test_finance_parsers.py`
- `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py`
- `/Users/akmini/Documents/minx-mcp/tests/test_finance_reports.py`
- `/Users/akmini/Documents/minx-mcp/tests/test_end_to_end.py`

**Why these files:**
- `money.py` is the single source of truth for runtime parse/format behavior.
- `004_finance_amount_cents.sql` performs the one-time schema and data migration.
- parser and importer files switch normalized data from `amount` to `amount_cents`.
- service and dedupe files remove remaining float-based persistence and fingerprinting.
- analytics and reports files aggregate in cents, then convert at the output edge.
- DB and test files verify migration sequencing, backfill correctness, and view compatibility.

### Task 1: Add Shared Money Helpers

**Files:**
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/money.py`
- Test: `/Users/akmini/Documents/minx-mcp/tests/test_money.py`

- [ ] **Step 1: Write the failing money helper tests**

Add these tests to `/Users/akmini/Documents/minx-mcp/tests/test_money.py`:

```python
import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.money import cents_to_dollars, format_cents, parse_dollars_to_cents


def test_parse_dollars_to_cents_accepts_exact_two_decimal_inputs():
    assert parse_dollars_to_cents("12.34") == 1234
    assert parse_dollars_to_cents("-42.16") == -4216
    assert parse_dollars_to_cents("0") == 0


def test_parse_dollars_to_cents_rejects_more_than_two_decimal_places():
    with pytest.raises(InvalidInputError, match="at most 2 decimal places"):
        parse_dollars_to_cents("12.345")


def test_cents_to_dollars_returns_display_floats():
    assert cents_to_dollars(1234) == 12.34
    assert cents_to_dollars(-4216) == -42.16


def test_format_cents_returns_currency_string():
    assert format_cents(1234) == "$12.34"
    assert format_cents(-4216) == "-$42.16"
```

- [ ] **Step 2: Run the money helper tests to verify they fail**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_money.py -v
```

Expected:

```text
FAIL because minx_mcp.money does not exist yet
```

- [ ] **Step 3: Implement the money helpers**

Create `/Users/akmini/Documents/minx-mcp/minx_mcp/money.py` with:

```python
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from minx_mcp.contracts import InvalidInputError

_CENT = Decimal("0.01")


def parse_dollars_to_cents(value: str) -> int:
    try:
        amount = Decimal(value.strip())
    except (AttributeError, InvalidOperation) as exc:
        raise InvalidInputError("amount must be a valid decimal string") from exc
    if amount.as_tuple().exponent < -2:
        raise InvalidInputError("amount must use at most 2 decimal places")
    return int((amount * 100).to_integral_exact())


def cents_to_dollars(value: int) -> float:
    return float(Decimal(value) / 100)


def format_cents(value: int) -> str:
    sign = "-" if value < 0 else ""
    dollars = Decimal(abs(value)) / 100
    return f"{sign}${dollars:.2f}"
```

- [ ] **Step 4: Run the money helper tests to verify they pass**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_money.py -v
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit the money helpers**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add minx_mcp/money.py tests/test_money.py && git commit -m "feat: add finance money helpers"
```

### Task 2: Add The Cents Migration And Bootstrap Coverage

**Files:**
- Create: `/Users/akmini/Documents/minx-mcp/schema/migrations/004_finance_amount_cents.sql`
- Create: `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/migrations/004_finance_amount_cents.sql`
- Modify: `/Users/akmini/Documents/minx-mcp/tests/test_db.py`

- [ ] **Step 1: Write the failing migration tests**

Add these tests to `/Users/akmini/Documents/minx-mcp/tests/test_db.py`:

```python
def test_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "minx.db"
    first = get_connection(db_path)
    first.close()
    second = get_connection(db_path)
    count = second.execute("SELECT COUNT(*) AS c FROM _migrations").fetchone()["c"]
    assert count == 4


def test_amount_cents_migration_backfills_existing_rows(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript((Path(__file__).resolve().parent.parent / "schema" / "migrations" / "001_platform.sql").read_text())
    conn.executescript((Path(__file__).resolve().parent.parent / "schema" / "migrations" / "002_finance.sql").read_text())
    conn.executescript((Path(__file__).resolve().parent.parent / "schema" / "migrations" / "003_finance_views.sql").read_text())
    conn.execute("CREATE TABLE _migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.executemany("INSERT INTO _migrations (name) VALUES (?)", [("001_platform.sql",), ("002_finance.sql",), ("003_finance_views.sql",)])
    conn.execute(
        '''
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount, category_id, category_source
        ) VALUES (1, 1, '2026-04-01', 'Legacy Amount', 'Store', -12.345, 1, 'uncategorized')
        '''
    )
    conn.execute(
        "INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint) VALUES (1, 1, 'csv', 'legacy.csv', 'fp')"
    )
    conn.commit()
    conn.close()

    migrated = get_connection(db_path)
    row = migrated.execute(
        "SELECT amount_cents FROM finance_transactions WHERE description = 'Legacy Amount'"
    ).fetchone()
    assert row["amount_cents"] == -1235

    spend = migrated.execute("SELECT total_amount FROM v_finance_monthly_spend").fetchone()
    assert float(spend["total_amount"]) == -12.35
```

- [ ] **Step 2: Run the database tests to verify they fail**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_db.py -v
```

Expected:

```text
FAIL because only 3 migrations exist and finance_transactions has no amount_cents column
```

- [ ] **Step 3: Implement migration 004 in both migration directories**

Create `/Users/akmini/Documents/minx-mcp/schema/migrations/004_finance_amount_cents.sql` and copy the same content to `/Users/akmini/Documents/minx-mcp/minx_mcp/schema/migrations/004_finance_amount_cents.sql`:

```sql
DROP VIEW IF EXISTS v_finance_monthly_spend;

CREATE TABLE finance_transactions_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES finance_accounts(id),
    batch_id INTEGER NOT NULL REFERENCES finance_import_batches(id),
    posted_at TEXT NOT NULL,
    description TEXT NOT NULL,
    merchant TEXT,
    amount_cents INTEGER NOT NULL,
    category_id INTEGER REFERENCES finance_categories(id),
    category_source TEXT NOT NULL DEFAULT 'uncategorized',
    external_id TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO finance_transactions_new (
    id,
    account_id,
    batch_id,
    posted_at,
    description,
    merchant,
    amount_cents,
    category_id,
    category_source,
    external_id,
    notes,
    created_at
)
SELECT
    id,
    account_id,
    batch_id,
    posted_at,
    description,
    merchant,
    CAST(ROUND(amount * 100, 0) AS INTEGER),
    category_id,
    category_source,
    external_id,
    notes,
    created_at
FROM finance_transactions;

DROP TABLE finance_transactions;
ALTER TABLE finance_transactions_new RENAME TO finance_transactions;

CREATE INDEX IF NOT EXISTS idx_finance_transactions_posted_at
ON finance_transactions(posted_at);

CREATE INDEX IF NOT EXISTS idx_finance_transactions_category
ON finance_transactions(category_id);

CREATE VIEW IF NOT EXISTS v_finance_monthly_spend AS
SELECT
    substr(posted_at, 1, 7) AS month,
    COALESCE(c.name, 'Uncategorized') AS category_name,
    SUM(t.amount_cents) / 100.0 AS total_amount
FROM finance_transactions t
LEFT JOIN finance_categories c ON c.id = t.category_id
GROUP BY substr(posted_at, 1, 7), COALESCE(c.name, 'Uncategorized');
```

- [ ] **Step 4: Run the database tests to verify they pass**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_db.py -v
```

Expected:

```text
PASS
```

- [ ] **Step 5: Commit the migration**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add schema/migrations/004_finance_amount_cents.sql minx_mcp/schema/migrations/004_finance_amount_cents.sql tests/test_db.py && git commit -m "feat: migrate finance storage to cents"
```

### Task 3: Convert Parsers, Importers, Service Inserts, And Dedupe To `amount_cents`

**Files:**
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/dcu.py`
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/discover.py`
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/generic_csv.py`
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/robinhood_gold.py`
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py`
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/dedupe.py`
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py`
- Test: `/Users/akmini/Documents/minx-mcp/tests/test_finance_parsers.py`
- Test: `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py`
- Test: `/Users/akmini/Documents/minx-mcp/tests/test_end_to_end.py`

- [ ] **Step 1: Write the failing parser and service tests**

Update `/Users/akmini/Documents/minx-mcp/tests/test_finance_parsers.py` with:

```python
def test_parse_dcu_csv_returns_amount_cents(tmp_path):
    source = tmp_path / "dcu.csv"
    source.write_text("Date,Description,Amount\n2026-03-28,HEB,-42.16\n")

    parsed = parse_dcu_csv(source, "DCU")

    assert parsed["transactions"][0]["amount_cents"] == -4216
    assert "amount" not in parsed["transactions"][0]


def test_generic_csv_rejects_more_than_two_decimals(tmp_path):
    source = tmp_path / "generic.csv"
    source.write_text("posted,description,amount\n03/28/2026,HEB,-12.345\n")

    with pytest.raises(InvalidInputError, match="at most 2 decimal places"):
        parse_generic_csv(
            source,
            "DCU",
            {
                "date_column": "posted",
                "date_format": "%m/%d/%Y",
                "description_column": "description",
                "amount_column": "amount",
            },
        )
```

Update `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py` with:

```python
def test_finance_import_stores_amount_cents(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", tmp_path)
    source = tmp_path / "dcu.csv"
    source.write_text("Date,Description,Amount\n2026-03-28,HEB,-12.50\n")

    service.finance_import(str(source), "DCU", source_kind="dcu_csv")

    transaction = service.conn.execute(
        "SELECT description, amount_cents FROM finance_transactions"
    ).fetchone()
    assert transaction["description"] == "HEB"
    assert transaction["amount_cents"] == -1250
```

- [ ] **Step 2: Run the parser and service tests to verify they fail**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_finance_parsers.py tests/test_finance_service.py::test_finance_import_stores_amount_cents -v
```

Expected:

```text
FAIL because parsers still emit amount and finance_transactions still reads amount
```

- [ ] **Step 3: Update parsers to emit `amount_cents`**

Apply these representative changes:

```python
# /Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/dcu.py
from minx_mcp.money import parse_dollars_to_cents

...
                {
                    "posted_at": row["Date"],
                    "description": row["Description"],
                    "merchant": row["Description"],
                    "amount_cents": parse_dollars_to_cents(row["Amount"]),
                }
```

```python
# /Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/discover.py
from minx_mcp.money import parse_dollars_to_cents

...
            amount_cents = -parse_dollars_to_cents(match.group("amount"))
            transactions.append(
                {
                    "posted_at": posted_at,
                    "description": description,
                    "merchant": description,
                    "amount_cents": amount_cents,
                }
            )
```

```python
# /Users/akmini/Documents/minx-mcp/minx_mcp/finance/parsers/generic_csv.py
from minx_mcp.money import parse_dollars_to_cents

...
            amount_cents = parse_dollars_to_cents(str(row[str(mapping["amount_column"])]))
            transactions.append(
                {
                    "posted_at": posted_at,
                    "description": description,
                    "merchant": merchant,
                    "amount_cents": -abs(amount_cents),
                }
            )
```

- [ ] **Step 4: Update importer validation, dedupe, and service inserts**

Apply these representative changes:

```python
# /Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py
        if "amount_cents" not in txn or not isinstance(txn["amount_cents"], int):
            raise InvalidInputError("parsed transactions must include integer amount_cents")
```

```python
# /Users/akmini/Documents/minx-mcp/minx_mcp/finance/dedupe.py
            str(int(transaction["amount_cents"])),
```

```python
# /Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py
            INSERT INTO finance_transactions (
                account_id, batch_id, posted_at, description, merchant, amount_cents,
                category_id, category_source, external_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'uncategorized', ?)
```

```python
# /Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py
                txn["amount_cents"],
```

- [ ] **Step 5: Run the parser, service, and end-to-end tests to verify they pass**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_finance_parsers.py tests/test_finance_service.py tests/test_end_to_end.py -v
```

Expected:

```text
PASS
```

- [ ] **Step 6: Commit the normalized cents refactor**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add minx_mcp/finance/parsers/dcu.py minx_mcp/finance/parsers/discover.py minx_mcp/finance/parsers/generic_csv.py minx_mcp/finance/parsers/robinhood_gold.py minx_mcp/finance/importers.py minx_mcp/finance/dedupe.py minx_mcp/finance/service.py tests/test_finance_parsers.py tests/test_finance_service.py tests/test_end_to_end.py && git commit -m "refactor: normalize finance imports to cents"
```

### Task 4: Convert Analytics And Reports To Aggregate In Cents But Return Dollars

**Files:**
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py`
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/reports.py`
- Test: `/Users/akmini/Documents/minx-mcp/tests/test_finance_reports.py`
- Test: `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py`

- [ ] **Step 1: Write the failing analytics and report tests**

Update `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py` with:

```python
def test_safe_finance_summary_returns_dollars_from_cents_storage(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", tmp_path)
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
```

Update `/Users/akmini/Documents/minx-mcp/tests/test_finance_reports.py` with:

```python
def test_weekly_report_aggregates_amount_cents_but_returns_dollars(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", tmp_path)
    service.conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'fp')
        """
    )
    service.conn.executemany(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, 1, "2026-03-28", "Paycheck", "Employer", 120000, 4, "manual"),
            (1, 1, "2026-03-29", "HEB", "HEB", -4216, 2, "manual"),
        ],
    )
    service.conn.commit()

    summary = build_weekly_report(service.conn, "2026-03-28", "2026-04-03")

    assert summary["totals"] == {"inflow": 1200.0, "outflow": 42.16}
```

- [ ] **Step 2: Run the analytics and report tests to verify they fail**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_finance_service.py tests/test_finance_reports.py -v
```

Expected:

```text
FAIL because analytics and reports still query amount instead of amount_cents
```

- [ ] **Step 3: Update analytics to sum cents and convert at the boundary**

Apply these representative changes in `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py`:

```python
from minx_mcp.money import cents_to_dollars

ANOMALY_THRESHOLD = -25_000


def summarize_finances(conn: Connection) -> dict[str, object]:
    total_cents = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total_cents FROM finance_transactions"
    ).fetchone()["total_cents"]
    category_rows = conn.execute(
        """
        SELECT c.name AS category_name, COALESCE(SUM(t.amount_cents), 0) AS total_cents
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        GROUP BY c.name
        ORDER BY total_cents ASC
        """
    ).fetchall()
    return {
        "net_total": cents_to_dollars(int(total_cents)),
        "categories": [
            {
                "category_name": row["category_name"],
                "total_amount": cents_to_dollars(int(row["total_cents"])),
            }
            for row in category_rows
        ],
    }
```

- [ ] **Step 4: Update reports to aggregate cents but expose dollars**

Apply these representative changes in `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/reports.py`:

```python
from minx_mcp.money import cents_to_dollars, format_cents

...
    totals_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents END), 0) AS inflow_cents,
            COALESCE(ABS(SUM(CASE WHEN amount_cents < 0 THEN amount_cents END)), 0) AS outflow_cents
        FROM finance_transactions
        WHERE posted_at >= ? AND posted_at < ?
        """,
        (period_start, end_exclusive),
    ).fetchone()
    totals = {
        "inflow": cents_to_dollars(int(totals_row["inflow_cents"])),
        "outflow": cents_to_dollars(int(totals_row["outflow_cents"])),
    }
```

```python
def _category_outflow_map(conn: Connection, period_start: str, end_exclusive: str) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(c.name, 'Uncategorized') AS category_name,
            COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_outflow_cents
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.posted_at >= ? AND t.posted_at < ? AND t.amount_cents < 0
        GROUP BY COALESCE(c.name, 'Uncategorized')
        """,
        (period_start, end_exclusive),
    ).fetchall()
    return {str(row["category_name"]): cents_to_dollars(int(row["total_outflow_cents"])) for row in rows}
```

```python
def _fmt_cents(amount_cents: int) -> str:
    return format_cents(amount_cents)
```

When updating markdown rendering, prefer formatting from cents-valued intermediate rows before dropping to float payloads. For example, if a render helper already has `total_outflow_cents`, call `_fmt_cents(int(total_outflow_cents))` directly instead of reconstructing cents from a float.

- [ ] **Step 5: Run analytics and report tests to verify they pass**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_finance_service.py tests/test_finance_reports.py -v
```

Expected:

```text
PASS
```

- [ ] **Step 6: Commit the cents aggregation refactor**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add minx_mcp/finance/analytics.py minx_mcp/finance/reports.py tests/test_finance_service.py tests/test_finance_reports.py && git commit -m "refactor: aggregate finance reports in cents"
```

### Task 5: Full Regression Verification

**Files:**
- Modify: `/Users/akmini/Documents/minx-mcp/tests/test_end_to_end.py`
- Modify: `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py`
- Modify: `/Users/akmini/Documents/minx-mcp/tests/test_finance_reports.py`
- Modify: `/Users/akmini/Documents/minx-mcp/tests/test_db.py`

- [ ] **Step 1: Add the final regression assertions**

Make sure the existing end-to-end and report tests include these final checks:

```python
def test_end_to_end_import_and_report_keeps_dollar_outputs(tmp_path):
    ...
    summary = service.safe_finance_summary()
    assert isinstance(summary["net_total"], float)
    assert summary["net_total"] == -42.16

    weekly = service.generate_weekly_report("2026-03-28", "2026-04-03")
    assert weekly["summary"]["totals"]["outflow"] == 42.16
```

```python
def test_monthly_spend_view_stays_dollar_facing(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    ...
    row = conn.execute("SELECT total_amount FROM v_finance_monthly_spend").fetchone()
    assert float(row["total_amount"]) == -12.35
```

- [ ] **Step 2: Run the targeted finance regression tests**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest tests/test_money.py tests/test_db.py tests/test_finance_parsers.py tests/test_finance_service.py tests/test_finance_reports.py tests/test_end_to_end.py -v
```

Expected:

```text
PASS
```

- [ ] **Step 3: Run the full suite**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && pytest -v
```

Expected:

```text
All tests pass with no amount-column failures and no MCP envelope regressions.
```

- [ ] **Step 4: Commit the final verification updates**

Run:

```bash
cd /Users/akmini/Documents/minx-mcp && git add tests/test_end_to_end.py tests/test_finance_service.py tests/test_finance_reports.py tests/test_db.py && git commit -m "test: cover finance cents migration end to end"
```
