# Minx MCP Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the finance MCP surface to a strict `{success, data, error, error_code}` envelope with shared contract helpers, typed errors, catch-all server wrapping, and updated tests.

**Architecture:** Add a shared `minx_mcp/contracts.py` module that owns response helpers, error codes, typed exceptions, and the server-facing wrapper. Keep transport-shape validation in `minx_mcp/finance/server.py`, move domain/state classification into `minx_mcp/finance/service.py`, and update finance tests to assert the envelope instead of raw exceptions at the MCP boundary.

**Tech Stack:** Python 3.12, FastMCP, pytest, SQLite

---

### Task 1: Add Shared Contract Helpers

**Files:**
- Create: `minx_mcp/contracts.py`
- Create: `tests/test_contracts.py`
- Modify: `minx_mcp/finance/server.py`

- [ ] **Step 1: Write the failing tests**

```python
import logging

from minx_mcp.contracts import (
    CONFLICT,
    INTERNAL_ERROR,
    INVALID_INPUT,
    ConflictError,
    InvalidInputError,
    fail,
    ok,
    wrap_tool_call,
)


def test_ok_wraps_success_payload():
    assert ok({"updated": 1}) == {
        "success": True,
        "data": {"updated": 1},
        "error": None,
        "error_code": None,
    }


def test_fail_wraps_error_payload():
    assert fail("bad input", INVALID_INPUT) == {
        "success": False,
        "data": None,
        "error": "bad input",
        "error_code": INVALID_INPUT,
    }


def test_wrap_tool_call_converts_contract_error_to_failure_envelope():
    def raise_invalid_input():
        raise InvalidInputError("bad input")

    result = wrap_tool_call(raise_invalid_input)

    assert result == {
        "success": False,
        "data": None,
        "error": "bad input",
        "error_code": INVALID_INPUT,
    }


def test_wrap_tool_call_logs_unexpected_exception_and_returns_internal_error(caplog):
    caplog.set_level(logging.ERROR)

    def raise_runtime_error():
        raise RuntimeError("boom")

    result = wrap_tool_call(raise_runtime_error)

    assert result == {
        "success": False,
        "data": None,
        "error": "Internal server error",
        "error_code": INTERNAL_ERROR,
    }
    assert "boom" in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError` for `minx_mcp.contracts`

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

import logging
from typing import Any, Callable

INVALID_INPUT = "INVALID_INPUT"
NOT_FOUND = "NOT_FOUND"
CONFLICT = "CONFLICT"
INTERNAL_ERROR = "INTERNAL_ERROR"

logger = logging.getLogger(__name__)


class MinxContractError(Exception):
    def __init__(self, message: str, error_code: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class InvalidInputError(MinxContractError):
    def __init__(self, message: str) -> None:
        super().__init__(message, INVALID_INPUT)


class NotFoundError(MinxContractError):
    def __init__(self, message: str) -> None:
        super().__init__(message, NOT_FOUND)


class ConflictError(MinxContractError):
    def __init__(self, message: str) -> None:
        super().__init__(message, CONFLICT)


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None, "error_code": None}


def fail(message: str, error_code: str) -> dict[str, Any]:
    return {"success": False, "data": None, "error": message, "error_code": error_code}


def wrap_tool_call(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return ok(fn())
    except MinxContractError as exc:
        return fail(exc.message, exc.error_code)
    except Exception:
        logger.exception("Unexpected exception in MCP tool")
        return fail("Internal server error", INTERNAL_ERROR)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_contracts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/contracts.py tests/test_contracts.py
git commit -m "feat: add shared MCP response contracts"
```

### Task 2: Apply The Validation Split In Finance Server

**Files:**
- Modify: `minx_mcp/finance/server.py`
- Test: `tests/test_finance_server.py`

- [ ] **Step 1: Write the failing server-boundary tests**

```python
def test_finance_categorize_tool_returns_invalid_input_envelope_for_empty_ids(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    finance_categorize = server._tool_manager.get_tool("finance_categorize").fn

    result = finance_categorize([], "Groceries")

    assert result == {
        "success": False,
        "data": None,
        "error": "transaction_ids must be a non-empty list",
        "error_code": "INVALID_INPUT",
    }


def test_report_tools_return_invalid_input_envelope_for_bad_date_window(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    weekly = server._tool_manager.get_tool("finance_generate_weekly_report").fn

    result = weekly("2026-03-10", "2026-03-01")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_finance_import_tool_returns_invalid_input_envelope_for_missing_source_file(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", import_root=tmp_path)
    server = create_finance_server(service)
    finance_import = server._tool_manager.get_tool("finance_import").fn

    result = finance_import(str(tmp_path / "missing.csv"), "DCU")

    assert result == {
        "success": False,
        "data": None,
        "error": "source_ref must point to an existing file",
        "error_code": "INVALID_INPUT",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_finance_server.py::test_finance_categorize_tool_returns_invalid_input_envelope_for_empty_ids tests/test_finance_server.py::test_report_tools_return_invalid_input_envelope_for_bad_date_window tests/test_finance_server.py::test_finance_import_tool_returns_invalid_input_envelope_for_missing_source_file -v`
Expected: FAIL because the tools currently raise `ValueError`

- [ ] **Step 3: Write minimal implementation**

```python
from minx_mcp.contracts import InvalidInputError, wrap_tool_call


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise InvalidInputError(f"{name} must not be empty")


def _validate_source_ref(source_ref: str) -> None:
    path = Path(source_ref)
    if not path.is_file():
        raise InvalidInputError("source_ref must point to an existing file")


def _validate_date_window(period_start: str, period_end: str) -> None:
    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError as exc:
        raise InvalidInputError("Invalid ISO date") from exc
    if start > end:
        raise InvalidInputError("period_start must be on or before period_end")
```

Then wrap each tool body:

```python
@mcp.tool(name="safe_finance_summary")
def safe_finance_summary() -> dict[str, object]:
    return wrap_tool_call(lambda: _safe_finance_summary(service))
```

and move the current body into a private helper:

```python
def _safe_finance_summary(service: object) -> dict[str, object]:
    with service:
        return service.safe_finance_summary()
```

Repeat this wrapper pattern for all finance tools:

- `safe_finance_summary`
- `safe_finance_accounts`
- `finance_import`
- `finance_categorize`
- `finance_add_category_rule`
- `finance_anomalies`
- `finance_job_status`
- `finance_generate_weekly_report`
- `finance_generate_monthly_report`
- `sensitive_finance_query`

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_finance_server.py::test_finance_categorize_tool_returns_invalid_input_envelope_for_empty_ids tests/test_finance_server.py::test_report_tools_return_invalid_input_envelope_for_bad_date_window tests/test_finance_server.py::test_finance_import_tool_returns_invalid_input_envelope_for_missing_source_file -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/finance/server.py tests/test_finance_server.py
git commit -m "feat: wrap finance MCP tools in response envelopes"
```

### Task 3: Add Typed Domain Errors In Finance Service

**Files:**
- Modify: `minx_mcp/finance/service.py`
- Test: `tests/test_finance_service.py`

- [ ] **Step 1: Write the failing service-layer tests**

```python
from minx_mcp.contracts import InvalidInputError, NotFoundError


def test_finance_categorize_rejects_empty_transaction_ids_with_invalid_input(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    with pytest.raises(InvalidInputError, match="transaction_ids must be a non-empty list"):
        service.finance_categorize([], "Groceries")


def test_sensitive_query_rejects_out_of_range_limit_with_invalid_input(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    with pytest.raises(InvalidInputError, match="limit must be between 1 and 500"):
        service.sensitive_finance_query(limit=0)


def test_missing_job_raises_not_found(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)

    with pytest.raises(NotFoundError, match="Unknown finance job id: missing-job"):
        service.get_job("missing-job")
```

Note: `FinanceService.get_job()` already exists in the current codebase. This task changes its missing-job behavior from returning `None` to raising `NotFoundError`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_finance_service.py::test_finance_categorize_rejects_empty_transaction_ids_with_invalid_input tests/test_finance_service.py::test_sensitive_query_rejects_out_of_range_limit_with_invalid_input tests/test_finance_service.py::test_missing_job_raises_not_found -v`
Expected: FAIL because the service currently raises `ValueError` or returns `None`

- [ ] **Step 3: Write minimal implementation**

```python
from minx_mcp.contracts import InvalidInputError, NotFoundError


def finance_categorize(self, transaction_ids: list[int], category_name: str) -> int:
    if not transaction_ids:
        raise InvalidInputError("transaction_ids must be a non-empty list")
    ...


def sensitive_finance_query(self, limit: int = 50, session_ref: str | None = None) -> dict[str, object]:
    if limit < 1 or limit > 500:
        raise InvalidInputError("limit must be between 1 and 500")
    return sensitive_query(self.conn, limit=limit, session_ref=session_ref)


def get_job(self, job_id: str) -> dict[str, object | None]:
    job = get_job(self.conn, job_id)
    if job is None:
        raise NotFoundError(f"Unknown finance job id: {job_id}")
    return job
```

Also replace these service `ValueError` cases with contract errors:

```python
raise InvalidInputError("source_ref must be inside the allowed import root")
raise InvalidInputError("Invalid ISO date")
raise InvalidInputError("weekly reports must span exactly 7 days")
raise NotFoundError(f"Unknown finance account: {account_name}")
raise NotFoundError(f"Unknown finance category: {category_name}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_finance_service.py::test_finance_categorize_rejects_empty_transaction_ids_with_invalid_input tests/test_finance_service.py::test_sensitive_query_rejects_out_of_range_limit_with_invalid_input tests/test_finance_service.py::test_missing_job_raises_not_found -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/finance/service.py tests/test_finance_service.py
git commit -m "feat: classify finance service errors"
```

### Task 4: Convert Finance Server Tests To Envelope Assertions

**Files:**
- Modify: `tests/test_finance_server.py`
- Modify: `tests/test_finance_service.py`

- [ ] **Step 1: Write the failing envelope assertions**

```python
def test_finance_job_status_returns_not_found_envelope_for_missing_job(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    finance_job_status = server._tool_manager.get_tool("finance_job_status").fn

    result = finance_job_status("missing-job")

    assert result == {
        "success": False,
        "data": None,
        "error": "Unknown finance job id: missing-job",
        "error_code": "NOT_FOUND",
    }


def test_finance_categorize_tool_reports_rows_updated_inside_data_envelope(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\\n2026-03-02,H-E-B,Withdrawal,-45.20\\n")
    service.finance_import(str(source), account_name="DCU")
    tx_id = service.sensitive_finance_query(limit=1)["transactions"][0]["id"]
    server = create_finance_server(service)
    finance_categorize = server._tool_manager.get_tool("finance_categorize").fn

    result = finance_categorize([tx_id, tx_id], "Groceries")

    assert result == {
        "success": True,
        "data": {"updated": 1},
        "error": None,
        "error_code": None,
    }
```

Note: this is intentionally an integration-style MCP boundary test, not a pure unit test. It exercises the real import plus categorize flow to prove the envelope survives a realistic server call.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_finance_server.py::test_finance_job_status_returns_not_found_envelope_for_missing_job tests/test_finance_server.py::test_finance_categorize_tool_reports_rows_updated_inside_data_envelope -v`
Expected: FAIL because the tool currently returns `None` or a non-envelope payload

- [ ] **Step 3: Write minimal implementation**

```python
missing = service.missing_transaction_ids(transaction_ids)
if missing:
    missing_list = ", ".join(str(transaction_id) for transaction_id in missing)
    raise NotFoundError(f"Unknown finance transaction ids: {missing_list}")
updated = service.finance_categorize(transaction_ids, category_name)
return {"updated": updated}
```

Combined with `wrap_tool_call`, this produces the final envelope.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_finance_server.py::test_finance_job_status_returns_not_found_envelope_for_missing_job tests/test_finance_server.py::test_finance_categorize_tool_reports_rows_updated_inside_data_envelope -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_finance_server.py tests/test_finance_service.py
git commit -m "test: assert finance MCP response envelopes"
```

### Task 5: Verify Unexpected Failures Are Logged And Wrapped

**Files:**
- Modify: `tests/test_finance_server.py`
- Modify: `minx_mcp/finance/server.py` or `minx_mcp/contracts.py`

- [ ] **Step 1: Write the failing unexpected-error test**

```python
def test_safe_finance_summary_returns_internal_error_envelope_for_unexpected_exception(tmp_path, monkeypatch, caplog):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    safe_summary = server._tool_manager.get_tool("safe_finance_summary").fn

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "safe_finance_summary", boom)
    caplog.set_level("ERROR")

    result = safe_summary()

    assert result == {
        "success": False,
        "data": None,
        "error": "Internal server error",
        "error_code": "INTERNAL_ERROR",
    }
    assert "boom" in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_finance_server.py::test_safe_finance_summary_returns_internal_error_envelope_for_unexpected_exception -v`
Expected: FAIL because the tool currently allows the exception to propagate or does not log it

- [ ] **Step 3: Write minimal implementation**

```python
except Exception:
    logger.exception("Unexpected exception in MCP tool")
    return fail("Internal server error", INTERNAL_ERROR)
```

If `wrap_tool_call` already implements this behavior from Task 1, keep the finance server unchanged and let the test pass through shared behavior.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_finance_server.py::test_safe_finance_summary_returns_internal_error_envelope_for_unexpected_exception -v`
Expected: PASS

- [ ] **Step 5: Commit**

If this task only adds verification coverage and no new production behavior beyond Task 1, fold the test changes into the final feature commit instead of creating a test-only checkpoint commit.

### Task 6: Full Verification

**Files:**
- Modify: `tests/test_finance_server.py`
- Modify: `tests/test_finance_service.py`
- Test: `tests/test_contracts.py`
- Test: `tests/test_end_to_end.py`

- [ ] **Step 1: Run focused finance and contract tests**

Run: `pytest tests/test_contracts.py tests/test_finance_server.py tests/test_finance_service.py tests/test_end_to_end.py -v`
Expected: PASS

- [ ] **Step 2: Deliberately migrate existing `ValueError` assertions**

Update these known tests so the migration is intentional rather than discovered accidentally at the end:

- `tests/test_finance_server.py::test_finance_import_tool_rejects_missing_source_file`
- `tests/test_finance_server.py::test_finance_import_tool_rejects_unknown_source_kind`
- `tests/test_finance_server.py::test_finance_import_tool_rejects_unsupported_file_before_reading_contents`
- `tests/test_finance_server.py::test_finance_categorize_tool_rejects_empty_transaction_ids`
- `tests/test_finance_server.py::test_finance_categorize_tool_rejects_unknown_transaction_ids`
- `tests/test_finance_server.py::test_finance_categorize_tool_rejects_unknown_category`
- `tests/test_finance_server.py::test_finance_add_category_rule_tool_rejects_empty_pattern`
- `tests/test_finance_server.py::test_finance_add_category_rule_tool_rejects_unknown_category`
- `tests/test_finance_server.py::test_report_tools_reject_invalid_date_windows`
- `tests/test_finance_server.py::test_sensitive_query_tool_rejects_large_limit`
- `tests/test_finance_server.py::test_finance_import_tool_rejects_paths_outside_allowed_import_root`
- `tests/test_finance_service.py::test_service_categorize_rejects_empty_transaction_ids`
- `tests/test_finance_service.py::test_service_sensitive_query_rejects_invalid_limits`
- `tests/test_finance_service.py::test_service_import_rejects_paths_outside_allowed_import_root`

Server-layer tests should assert the envelope. Service-layer tests should assert typed contract exceptions.

- [ ] **Step 3: Run the full test suite**

Run: `pytest -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add minx_mcp/contracts.py minx_mcp/finance/server.py minx_mcp/finance/service.py tests/test_contracts.py tests/test_finance_server.py tests/test_finance_service.py
git commit -m "feat: standardize MCP response contracts"
```
