**Status: Completed (historical).** This plan was executed in an earlier slice. The codebase has since evolved — see [HANDOFF.md](/Users/akmini/Documents/minx-mcp/HANDOFF.md) for current state.

# Minx MCP Stabilization And Publish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize `minx-mcp` into a clean, usable, publishable state by fixing report durability semantics, replacing the worst stringly-typed finance internals with typed models, tightening verification, and preparing a clean push to `main`.

**Architecture:** Keep `FinanceService` as the orchestration boundary, keep MCP response shapes stable where practical, and move cleanup behind finance internal seams. Add explicit report lifecycle state to `finance_report_runs`, make vault markdown writes atomic, refactor reports/importers/parsers around typed internal models, and verify the full setup/startup/end-to-end path before publishing.

**Tech Stack:** Python 3.12, SQLite, dataclasses, pytest, mypy, FastMCP, existing `VaultWriter`, existing finance/core modules

---

## File Structure

**Create**

- `minx_mcp/schema/migrations/006_finance_report_lifecycle.sql`
  Add report lifecycle columns and uniqueness for one logical report window.
- `minx_mcp/finance/report_models.py`
  Typed weekly/monthly report dataclasses and serialization helpers.
- `minx_mcp/finance/import_models.py`
  Typed parsed import and mapping dataclasses.
- `tests/test_report_lifecycle.py`
  Focused regressions for pending/completed/failed report runs and retry/repair behavior.

**Modify**

- `tests/test_db.py`
  Cover the new migration and lifecycle columns/indexes.
- `minx_mcp/vault_writer.py`
  Make markdown writes atomic by default without weakening path safety.
- `tests/test_vault_writer.py`
  Cover atomic overwrite behavior.
- `minx_mcp/finance/reports.py`
  Build/report typed models instead of nested `dict[str, object]`.
- `tests/test_finance_reports.py`
  Adjust report assertions to work through stable outward summaries while validating typed internals where appropriate.
- `minx_mcp/finance/importers.py`
  Return typed parsed batches and validate at the typed boundary.
- `minx_mcp/finance/parsers/dcu.py`
- `minx_mcp/finance/parsers/discover.py`
- `minx_mcp/finance/parsers/generic_csv.py`
- `minx_mcp/finance/parsers/robinhood_gold.py`
  Return typed parsed batches/transactions instead of loose dicts.
- `tests/test_finance_parsers.py`
  Cover typed parser outputs and generic CSV mapping validation.
- `minx_mcp/finance/service.py`
  Use the new typed report/import models and implement explicit report lifecycle flow.
- `tests/test_finance_service.py`
  Cover service integration with typed parsed imports and stable report results.
- `tests/test_finance_events.py`
  Update report event and failure semantics around lifecycle state.
- `tests/test_end_to_end.py`
  Verify import -> categorize -> report -> daily review path still works.
- `pyproject.toml`
  Expand the mypy target set to cover the cleaned finance internals.
- `README.md`
  Refresh setup/run/verify docs for a user-facing published repo.

**Leave Untouched Unless Required**

- `HANDOFF.md`
- `docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md`
- `docs/superpowers/specs/2026-04-06-slice1-event-pipeline-daily-review-design.md`
- `ARCHITECTURE.md`

Those files are currently unrelated working-tree noise and should not be mixed into the stabilization implementation unless the user explicitly asks.

## Task 1: Add Report Lifecycle Migration

**Files:**
- Create: `minx_mcp/schema/migrations/006_finance_report_lifecycle.sql`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Add a failing DB bootstrap test for the new lifecycle columns and unique identity**

```python
def test_database_bootstrap_creates_finance_report_lifecycle_columns(tmp_path):
    from minx_mcp.db import get_connection

    conn = get_connection(tmp_path / "minx.db")
    columns = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(finance_report_runs)").fetchall()
    }
    indexes = {
        row["name"]
        for row in conn.execute("PRAGMA index_list(finance_report_runs)").fetchall()
    }

    assert "status" in columns
    assert "updated_at" in columns
    assert "error_message" in columns
    assert "idx_finance_report_runs_identity" in indexes
```

- [ ] **Step 2: Add a failing migration-count assertion update**

```python
def test_migrations_are_idempotent(tmp_path):
    from minx_mcp.db import get_connection

    conn = get_connection(tmp_path / "minx.db")
    applied = conn.execute("SELECT name FROM _migrations ORDER BY name").fetchall()

    assert [row["name"] for row in applied] == [
        "001_platform.sql",
        "002_finance.sql",
        "003_finance_views.sql",
        "004_finance_amount_cents.sql",
        "005_core.sql",
        "006_finance_report_lifecycle.sql",
    ]
```

- [ ] **Step 3: Run the DB test file to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: FAIL because the lifecycle columns/index do not exist yet.

- [ ] **Step 4: Implement the migration**

```sql
ALTER TABLE finance_report_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'completed';
ALTER TABLE finance_report_runs ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'));
ALTER TABLE finance_report_runs ADD COLUMN error_message TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_finance_report_runs_identity
ON finance_report_runs(report_kind, period_start, period_end);
```

Also backfill `updated_at` and keep existing rows valid under the new `completed` default.

- [ ] **Step 5: Run the DB test file to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_db.py minx_mcp/schema/migrations/006_finance_report_lifecycle.sql
git commit -m "feat: add finance report lifecycle migration"
```

## Task 2: Make Vault Markdown Writes Atomic

**Files:**
- Modify: `minx_mcp/vault_writer.py`
- Modify: `tests/test_vault_writer.py`

- [ ] **Step 1: Add a failing overwrite test that exercises atomic replacement semantics**

```python
def test_write_markdown_atomically_replaces_existing_file(tmp_path):
    from minx_mcp.vault_writer import VaultWriter

    writer = VaultWriter(tmp_path, ("Finance",))
    path = writer.write_markdown("Finance/report.md", "old")

    replaced = writer.write_markdown("Finance/report.md", "new")

    assert replaced == path
    assert path.read_text() == "new"
```

- [ ] **Step 2: Run the vault writer tests to verify the new test is red or meaningfully incomplete**

Run: `.venv/bin/python -m pytest tests/test_vault_writer.py -v`
Expected: FAIL or expose that writes are direct and not explicitly atomic.

- [ ] **Step 3: Change `write_markdown()` to use temp-file-plus-rename in the target directory**

```python
def write_markdown(self, relative_path: str, content: str) -> Path:
    path = self._resolve(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        handle.write(content)
        temp_path = Path(handle.name)

    temp_path.replace(path)
    return path
```

Wrap cleanup so abandoned temp files are removed on failure.

- [ ] **Step 4: Run the vault writer tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_vault_writer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/vault_writer.py tests/test_vault_writer.py
git commit -m "feat: make vault markdown writes atomic"
```

## Task 3: Add Typed Report Models

**Files:**
- Create: `minx_mcp/finance/report_models.py`
- Modify: `minx_mcp/finance/reports.py`
- Modify: `tests/test_finance_reports.py`

- [ ] **Step 1: Add a failing test that requires typed weekly report output from the builder layer**

```python
def test_build_weekly_report_returns_typed_summary(tmp_path):
    from minx_mcp.finance.report_models import WeeklyReportSummary
    from minx_mcp.finance.reports import build_weekly_report
    from minx_mcp.finance.service import FinanceService

    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    summary = build_weekly_report(service.conn, "2026-03-02", "2026-03-08")

    assert isinstance(summary, WeeklyReportSummary)
```

- [ ] **Step 2: Add a failing test for typed monthly review items**

```python
def test_build_monthly_report_returns_typed_review_items(tmp_path):
    from minx_mcp.finance.report_models import MonthlyReportSummary, NewMerchantReviewItem
    from minx_mcp.finance.service import FinanceService
    from minx_mcp.finance.reports import build_monthly_report

    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    summary = build_monthly_report(service.conn, "2026-03-01", "2026-03-31")

    assert isinstance(summary, MonthlyReportSummary)
    assert all(hasattr(item, "kind") for item in summary.uncategorized_or_new_merchants)
```

- [ ] **Step 3: Run the report tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_finance_reports.py -v`
Expected: FAIL because the builders still return plain dicts.

- [ ] **Step 4: Add typed report dataclasses and serialization helpers**

```python
@dataclass(frozen=True)
class MoneyTotals:
    inflow: float
    outflow: float


@dataclass(frozen=True)
class WeeklyCategoryChange:
    category_name: str
    current_outflow: float
    prior_outflow: float
    delta_outflow: float


@dataclass(frozen=True)
class WeeklyReportSummary:
    period_start: str
    period_end: str
    totals: MoneyTotals
    top_categories: list[TopCategory]
    notable_merchants: list[NotableMerchant]
    category_changes: list[WeeklyCategoryChange]
    anomalies: list[dict[str, object]]
    uncategorized_transactions: list[dict[str, object]]
```

Also add `to_dict()` helpers so service and JSON storage can keep outward compatibility.

- [ ] **Step 5: Refactor `build_weekly_report()`, `build_monthly_report()`, and the render functions to operate on typed models**

```python
def build_weekly_report(conn: Connection, period_start: str, period_end: str) -> WeeklyReportSummary:
    return WeeklyReportSummary(
        period_start=period_start,
        period_end=period_end,
        totals=MoneyTotals(inflow=..., outflow=...),
        top_categories=[...],
        notable_merchants=[...],
        category_changes=[...],
        anomalies=find_anomalies(conn, period_start, end_exclusive),
        uncategorized_transactions=find_uncategorized(conn, period_start, end_exclusive),
    )
```

- [ ] **Step 6: Run the report tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_finance_reports.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add minx_mcp/finance/report_models.py minx_mcp/finance/reports.py tests/test_finance_reports.py
git commit -m "feat: add typed finance report models"
```

## Task 4: Add Typed Import And Parser Models

**Files:**
- Create: `minx_mcp/finance/import_models.py`
- Modify: `minx_mcp/finance/importers.py`
- Modify: `minx_mcp/finance/parsers/dcu.py`
- Modify: `minx_mcp/finance/parsers/discover.py`
- Modify: `minx_mcp/finance/parsers/generic_csv.py`
- Modify: `minx_mcp/finance/parsers/robinhood_gold.py`
- Modify: `tests/test_finance_parsers.py`

- [ ] **Step 1: Add a failing parser test that expects a typed parsed batch**

```python
def test_parse_dcu_csv_returns_typed_parsed_batch(tmp_path):
    from minx_mcp.finance.import_models import ParsedImportBatch
    from minx_mcp.finance.parsers.dcu import parse_dcu_csv

    path = tmp_path / "free checking transactions.csv"
    path.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")

    parsed = parse_dcu_csv(path, "DCU")

    assert isinstance(parsed, ParsedImportBatch)
    assert parsed.transactions[0].amount_cents == -4520
```

- [ ] **Step 2: Add a failing generic CSV mapping validation test**

```python
def test_parse_generic_csv_requires_typed_mapping_fields(tmp_path):
    from minx_mcp.contracts import InvalidInputError
    from minx_mcp.finance.parsers.generic_csv import parse_generic_csv

    path = tmp_path / "generic.csv"
    path.write_text("date,desc,amount\n2026-03-02,Coffee,-4.00\n")

    with pytest.raises((InvalidInputError, KeyError, TypeError)):
        parse_generic_csv(path, "DCU", {"date_column": "date"})
```

- [ ] **Step 3: Run the parser test file to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_finance_parsers.py -v`
Expected: FAIL because parsers still return plain dicts.

- [ ] **Step 4: Add typed parsed import dataclasses**

```python
@dataclass(frozen=True)
class ParsedTransaction:
    posted_at: str
    description: str
    merchant: str | None
    amount_cents: int
    category_hint: str | None
    external_id: str | None


@dataclass(frozen=True)
class ParsedImportBatch:
    account_name: str
    source_type: str
    source_ref: str
    raw_fingerprint: str
    transactions: list[ParsedTransaction]
```

- [ ] **Step 5: Update each parser and `parse_source_file()` to return/validate typed batches**

```python
def parse_source_file(...) -> ParsedImportBatch:
    ...
    _validate_parsed_transactions(result)
    return replace(result, source_ref=str(path.resolve()), raw_fingerprint=content_hash)
```

Use `dataclasses.replace()` instead of mutating dicts.

- [ ] **Step 6: Run the parser test file to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_finance_parsers.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add minx_mcp/finance/import_models.py minx_mcp/finance/importers.py minx_mcp/finance/parsers/dcu.py minx_mcp/finance/parsers/discover.py minx_mcp/finance/parsers/generic_csv.py minx_mcp/finance/parsers/robinhood_gold.py tests/test_finance_parsers.py
git commit -m "feat: add typed finance import models"
```

## Task 5: Implement Report Lifecycle In The Service Layer

**Files:**
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/finance/reports.py`
- Create: `tests/test_report_lifecycle.py`
- Modify: `tests/test_finance_events.py`
- Modify: `tests/test_finance_service.py`

- [ ] **Step 1: Add a failing lifecycle test for `pending` -> `completed`**

```python
def test_generate_weekly_report_marks_run_pending_then_completed(tmp_path):
    from minx_mcp.finance.service import FinanceService

    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    result = service.generate_weekly_report("2026-03-02", "2026-03-08")

    row = service.conn.execute(
        """
        SELECT status, error_message, vault_path
        FROM finance_report_runs
        WHERE report_kind = 'weekly' AND period_start = '2026-03-02' AND period_end = '2026-03-08'
        """
    ).fetchone()

    assert row["status"] == "completed"
    assert row["error_message"] is None
    assert row["vault_path"] == result["vault_path"]
```

- [ ] **Step 2: Add a failing lifecycle test for failure-after-write repair state**

```python
def test_generate_weekly_report_marks_run_failed_when_post_write_db_step_fails(tmp_path, monkeypatch):
    from minx_mcp.finance.service import FinanceService

    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")

    def fail_emit(*args, **kwargs):
        raise RuntimeError("event blocked")

    monkeypatch.setattr(service, "_emit_finance_event", fail_emit)

    with pytest.raises(RuntimeError, match="event blocked"):
        service.generate_weekly_report("2026-03-02", "2026-03-08")

    row = service.conn.execute(
        """
        SELECT status, error_message
        FROM finance_report_runs
        WHERE report_kind = 'weekly' AND period_start = '2026-03-02' AND period_end = '2026-03-08'
        """
    ).fetchone()

    assert row["status"] == "failed"
    assert "event blocked" in row["error_message"]
```

- [ ] **Step 3: Add a failing lifecycle test for rerun repair**

```python
def test_generate_weekly_report_repairs_failed_row_on_rerun(tmp_path, monkeypatch):
    from minx_mcp.finance.service import FinanceService

    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")

    monkeypatch.setattr(service, "_emit_finance_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")))
    with pytest.raises(RuntimeError):
        service.generate_weekly_report("2026-03-02", "2026-03-08")

    monkeypatch.setattr(service, "_emit_finance_event", lambda *args, **kwargs: 1)
    service.generate_weekly_report("2026-03-02", "2026-03-08")

    rows = service.conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM finance_report_runs
        WHERE report_kind = 'weekly' AND period_start = '2026-03-02' AND period_end = '2026-03-08'
        """
    ).fetchone()

    assert rows["count"] == 1
```

- [ ] **Step 4: Run the targeted report lifecycle tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_report_lifecycle.py tests/test_finance_events.py -v`
Expected: FAIL because the service does not yet track explicit report lifecycle state.

- [ ] **Step 5: Add lifecycle helpers in `reports.py`**

```python
def upsert_report_run(..., status: str, error_message: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO finance_report_runs (...)
        VALUES (...)
        ON CONFLICT(report_kind, period_start, period_end)
        DO UPDATE SET
            vault_path = excluded.vault_path,
            summary_json = excluded.summary_json,
            status = excluded.status,
            updated_at = datetime('now'),
            error_message = excluded.error_message
        """
    )
```

Also add explicit `mark_report_completed()` and `mark_report_failed()` helpers if that keeps service orchestration simpler.

- [ ] **Step 6: Refactor service report generation flow around the new lifecycle**

```python
summary = build_weekly_report(self.conn, period_start, period_end)
content = render_weekly_markdown(summary, period_start, period_end)
upsert_report_run(self.conn, "weekly", period_start, period_end, str(target_path), summary.to_dict(), status="pending")
path = self.vault_writer.write_markdown(relative_path, content)
try:
    self._emit_finance_event(...)
    mark_report_completed(self.conn, "weekly", period_start, period_end, str(path), summary.to_dict())
    self.conn.commit()
except Exception as exc:
    _best_effort_unlink(path)
    mark_report_failed(self.conn, "weekly", period_start, period_end, str(path), summary.to_dict(), str(exc))
    self.conn.commit()
    raise
```

- [ ] **Step 7: Run the targeted report lifecycle tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_report_lifecycle.py tests/test_finance_events.py tests/test_finance_service.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add minx_mcp/finance/service.py minx_mcp/finance/reports.py tests/test_report_lifecycle.py tests/test_finance_events.py tests/test_finance_service.py
git commit -m "feat: add explicit finance report lifecycle"
```

## Task 6: Integrate Typed Import Models Into Service

**Files:**
- Modify: `minx_mcp/finance/service.py`
- Modify: `tests/test_finance_service.py`

- [ ] **Step 1: Add a failing service integration test that touches typed parsed transactions**

```python
def test_finance_import_consumes_typed_parsed_batch(tmp_path, monkeypatch):
    from minx_mcp.finance.import_models import ParsedImportBatch, ParsedTransaction
    from minx_mcp.finance.service import FinanceService

    service = FinanceService(tmp_path / "minx.db", tmp_path)

    def fake_parse(*args, **kwargs):
        return ParsedImportBatch(
            account_name="DCU",
            source_type="csv",
            source_ref="x",
            raw_fingerprint="fp",
            transactions=[
                ParsedTransaction(
                    posted_at="2026-03-02",
                    description="H-E-B",
                    merchant="H-E-B",
                    amount_cents=-4520,
                    category_hint=None,
                    external_id=None,
                )
            ],
        )

    monkeypatch.setattr("minx_mcp.finance.service.parse_source_file", fake_parse)
```

Assert the import still inserts a transaction and produces the expected job result.

- [ ] **Step 2: Run the targeted service tests to verify they fail or expose dict assumptions**

Run: `.venv/bin/python -m pytest tests/test_finance_service.py -v`
Expected: FAIL or expose remaining dict-indexed assumptions in service import flow.

- [ ] **Step 3: Refactor `_insert_batch()` and `_insert_transaction()` to consume typed models**

```python
def _insert_batch(self, account_id: int, parsed: ParsedImportBatch) -> int: ...

def _insert_transaction(
    self,
    account_id: int,
    batch_id: int,
    txn: ParsedTransaction,
) -> int: ...
```

- [ ] **Step 4: Run the targeted service tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_finance_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/finance/service.py tests/test_finance_service.py
git commit -m "refactor: use typed parsed imports in finance service"
```

## Task 7: Expand Static Typing Coverage

**Files:**
- Modify: `pyproject.toml`
- Run only: mypy over cleaned finance internals

- [ ] **Step 1: Expand the mypy file list to include the cleaned finance internals**

```toml
[tool.mypy]
files = [
  "minx_mcp/finance/server.py",
  "minx_mcp/finance/analytics.py",
  "minx_mcp/finance/import_models.py",
  "minx_mcp/finance/importers.py",
  "minx_mcp/finance/report_models.py",
  "minx_mcp/finance/reports.py",
  "minx_mcp/finance/service.py",
  "minx_mcp/vault_writer.py",
]
```

- [ ] **Step 2: Run mypy and fix the resulting typing gaps**

Run: `.venv/bin/python -m mypy`
Expected: Initial FAIL with finance-internal typing gaps that must be resolved.

- [ ] **Step 3: Re-run mypy until it passes**

Run: `.venv/bin/python -m mypy`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml minx_mcp/finance/import_models.py minx_mcp/finance/importers.py minx_mcp/finance/report_models.py minx_mcp/finance/reports.py minx_mcp/finance/service.py minx_mcp/vault_writer.py
git commit -m "chore: expand mypy coverage for finance internals"
```

## Task 8: Refresh Publish-Facing Docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a failing expectation checklist for the README content**

Create a local checklist and verify the README includes:

- setup
- editable install
- stdio startup
- HTTP startup
- tests
- type-check command
- brief known limitations

- [ ] **Step 2: Rewrite the README to be publish-facing and concise**

```md
# minx-mcp

Shared Minx MCP platform with a finance domain and daily review pipeline.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Verify

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy
```
```

Also include a short known-limitations section that is honest but compact.

- [ ] **Step 3: Manually review the README against the checklist**

Expected: all checklist items covered with no WIP or handoff language.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: refresh publish-facing README"
```

## Task 9: Full Verification And Publish Cleanup

**Files:**
- Modify only if needed: tests or runtime code discovered during verification

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 2: Run the full mypy target set**

Run: `.venv/bin/python -m mypy`
Expected: PASS

- [ ] **Step 3: Verify editable install from the current checkout**

Run: `.venv/bin/pip install -e '.[dev]'`
Expected: PASS

- [ ] **Step 4: Verify stdio startup smoke**

Run: `.venv/bin/python -m minx_mcp.finance --transport stdio`
Expected: process starts without immediate traceback and remains alive until interrupted.

- [ ] **Step 5: Verify HTTP startup smoke**

Run: `.venv/bin/python -m minx_mcp.finance --transport http --host 127.0.0.1 --port 8765`
Expected: process starts without immediate traceback and binds successfully.

- [ ] **Step 6: Run one end-to-end usage path**

Use the existing end-to-end test and, if needed, one manual smoke path:

Run: `.venv/bin/python -m pytest tests/test_end_to_end.py -v`
Expected: PASS

- [ ] **Step 7: Clean publish state**

Verify the branch contains only intentional code/docs changes for publish. Do not auto-stage these unrelated working-tree files unless the user explicitly wants them:

```text
HANDOFF.md
docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md
docs/superpowers/specs/2026-04-06-slice1-event-pipeline-daily-review-design.md
ARCHITECTURE.md
docs/superpowers/plans/2026-04-06-slice1-event-pipeline-daily-review.md
```

- [ ] **Step 8: Commit any final verification-driven fixes**

```bash
git add README.md pyproject.toml minx_mcp tests
git commit -m "chore: stabilize project for publish"
```

## Task 10: Fast-Forward To Main And Push

**Files:**
- No source edits; git operations only

- [ ] **Step 1: Check branch state before switching**

Run: `git status --short --branch`
Expected: no uncommitted intended code changes remain.

- [ ] **Step 2: Switch to `main`**

Run: `git checkout main`
Expected: branch switch succeeds without discarding unrelated user work.

- [ ] **Step 3: Fast-forward or merge the verified branch result**

Run: `git merge --ff-only codex/slice1-event-pipeline-daily-review`
Expected: `main` now points at the verified stabilization commits.

- [ ] **Step 4: Push `main`**

Run: `git push origin main`
Expected: remote `main` updated successfully.

- [ ] **Step 5: Re-run final status check**

Run: `git status --short --branch`
Expected: clean or only explicitly preserved local-only noise.
