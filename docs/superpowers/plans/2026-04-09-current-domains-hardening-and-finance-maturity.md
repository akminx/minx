**Status: Completed (historical).** This plan was executed in an earlier slice. The codebase has since evolved — see [HANDOFF.md](/Users/akmini/Documents/minx-mcp/HANDOFF.md) for current state.

# Current Domains Hardening And Finance Maturity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the current correctness gaps in Core/Finance/Goals, make the interpretation and audit layers production-real, and then layer in the finance maturity work that makes the current domains comfortable enough to move on from.

**Architecture:** Phase 1 keeps the current Minx split intact while making the shared interpretation layer real at the actual MCP boundaries: Core owns interpretation, Finance owns facts and monitoring, Goals owns goal truth, and transports remain thin. Later phases add merchant normalization, staged rules, import preview, stronger monitoring, and goal-supporting insights without turning Minx into a budgeting app or a generalized agent runtime.

**Tech Stack:** Python 3.12, SQLite, FastMCP, Pydantic, pytest, mypy, existing `openai_compatible` LLM path

---

## File Structure

### New files

- `docs/superpowers/plans/2026-04-09-current-domains-hardening-and-finance-maturity.md`
  Responsibility: execution plan for the approved current-domains design.
- `minx_mcp/core/interpretation/context.py`
  Responsibility: compact shared context builders for goal capture and finance query interpretation.
- `minx_mcp/core/interpretation/logging.py`
  Responsibility: redacted interpretation logging helpers and event payload builders.
- `minx_mcp/finance/normalization.py`
  Responsibility: canonical merchant normalization, alias matching, and raw-vs-canonical merchant utilities.
- `minx_mcp/finance/rules.py`
  Responsibility: staged deterministic finance rule evaluation over normalized transactions.
- `tests/test_interpretation_logging.py`
  Responsibility: interpretation logging and redaction coverage.
- `tests/test_finance_rules.py`
  Responsibility: staged rules and normalization coverage.
- `tests/test_finance_import_preview.py`
  Responsibility: import preview / dry-run behavior.
- `tests/test_finance_monitoring.py`
  Responsibility: category/merchant/income monitoring read-model coverage.
- `tests/test_goal_finance_insights.py`
  Responsibility: goal-supporting finance insight coverage.

### Modified files

- `minx_mcp/core/server.py`
  Responsibility: real Core MCP tool wiring, especially async-safe `goal_capture`.
- `minx_mcp/core/goal_capture.py`
  Responsibility: async-safe goal capture interpretation adapter and deterministic fallback behavior.
- `minx_mcp/core/interpretation/models.py`
  Responsibility: stricter schema-level consistency for interpretation outputs.
- `minx_mcp/core/interpretation/runner.py`
  Responsibility: shared structured interpretation execution.
- `minx_mcp/core/interpretation/finance_query.py`
  Responsibility: async-safe finance query interpretation and deterministic clarify behavior.
- `minx_mcp/core/llm.py`
  Responsibility: reusable JSON-backed LLM interface and provider loading.
- `minx_mcp/core/llm_openai.py`
  Responsibility: JSON prompt execution for the OpenAI-compatible provider.
- `minx_mcp/core/models.py`
  Responsibility: shared interpretation and query-plan types.
- `minx_mcp/finance/server.py`
  Responsibility: sensitive finance MCP tool validation, audit coverage, and import preview/query surfaces.
- `minx_mcp/finance/service.py`
  Responsibility: finance domain operations, normalization, rules, preview, monitoring, and insights.
- `minx_mcp/finance/analytics.py`
  Responsibility: deterministic filtered query execution, monitoring, and audit-aware aggregate reads.
- `minx_mcp/finance/importers.py`
  Responsibility: import source identification and parser orchestration.
- `minx_mcp/finance/import_workflow.py`
  Responsibility: detect -> map -> preview -> import workflow.
- `minx_mcp/finance/read_api.py`
  Responsibility: deterministic finance reads used by Goals and review logic.
- `minx_mcp/preferences.py`
  Responsibility: preference-backed finance thresholds and future normalization/rule config helpers.
- `tests/test_core_server.py`
  Responsibility: end-to-end Core MCP boundary coverage for goal capture.
- `tests/test_goal_capture.py`
  Responsibility: unit-level goal capture interpretation behavior.
- `tests/test_finance_query_interpretation.py`
  Responsibility: finance query plan interpretation tests.
- `tests/test_finance_server.py`
  Responsibility: MCP boundary validation, clarify contracts, and audit expectations.
- `tests/test_finance_service.py`
  Responsibility: service-level query, rules, normalization, and import behaviors.
- `tests/test_finance_parsers.py`
  Responsibility: importer identification and parser coverage.

## Phase 1: Correctness And Foundation Hardening

### Task 1: Make Goal Capture LLM Wiring Real At The Core MCP Boundary

**Files:**
- Modify: `minx_mcp/core/server.py`
- Modify: `minx_mcp/core/goal_capture.py`
- Modify: `minx_mcp/core/llm.py`
- Test: `tests/test_core_server.py`
- Test: `tests/test_goal_capture.py`

- [ ] **Step 1: Write the failing boundary tests**

```python
def test_goal_capture_tool_uses_configured_llm_when_available(tmp_path, monkeypatch):
    class _StubLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return (
                '{"intent":"create","confidence":0.93,"subject_kind":"merchant",'
                '"subject":"Amazon","period":"monthly","target_value":20000}'
            )

    monkeypatch.setattr("minx_mcp.core.server.create_llm", lambda **_: _StubLLM())
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    result = server._tool_manager.get_tool("goal_capture").fn(
        message="track my Amazon spending under $200 monthly",
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"
    assert result["data"]["payload"]["merchant_names"] == ["Amazon"]


def test_goal_capture_tool_falls_back_when_llm_returns_malformed_payload(tmp_path, monkeypatch):
    class _BadLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return '{"intent":"create"}'

    monkeypatch.setattr("minx_mcp.core.server.create_llm", lambda **_: _BadLLM())
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    result = server._tool_manager.get_tool("goal_capture").fn(
        message="Make a goal to spend less than $25 on dining out",
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"
    assert result["data"]["payload"]["category_names"] == ["Dining Out"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_core_server.py tests/test_goal_capture.py -q`
Expected: FAIL because the real `goal_capture` tool path still never passes an LLM into `capture_goal_message`.

- [ ] **Step 3: Thread the configured LLM through the real Core server path**

```python
# minx_mcp/core/server.py
from minx_mcp.core.llm import create_llm


def _goal_capture(
    config: CoreServiceConfig,
    message: str,
    review_date: str | None,
) -> dict[str, object]:
    ...
    llm = create_llm(db_path=config.db_path)
    result = capture_goal_message(
        message=normalized_message,
        review_date=effective_review_date,
        finance_api=FinanceReadAPI(conn),
        goals=goals,
        llm=llm,
    )
    return _goal_capture_result_to_dict(result)
```

```python
# minx_mcp/core/goal_capture.py
def capture_goal_message(..., llm: object | None = None) -> GoalCaptureResult:
    if llm is not None:
        interpreted = ...  # keep deterministic fallback if LLM fails
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_core_server.py tests/test_goal_capture.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/core/server.py minx_mcp/core/goal_capture.py minx_mcp/core/llm.py tests/test_core_server.py tests/test_goal_capture.py
git commit -m "fix: wire llm-backed goal capture through core server"
```

### Task 2: Remove Nested Event Loops From Interpretation Helpers

**Files:**
- Modify: `minx_mcp/core/goal_capture.py`
- Modify: `minx_mcp/core/interpretation/finance_query.py`
- Modify: `minx_mcp/core/server.py`
- Modify: `minx_mcp/finance/server.py`
- Test: `tests/test_goal_capture.py`
- Test: `tests/test_finance_query_interpretation.py`
- Test: `tests/test_finance_server.py`

- [ ] **Step 1: Write failing async-safety tests**

```python
import pytest


@pytest.mark.asyncio
async def test_goal_capture_interpretation_is_safe_inside_running_event_loop() -> None:
    result = await interpret_goal_capture_message(
        message="track Amazon under $200 monthly",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
        llm=_StubGoalCaptureLLM(...),
    )
    assert result.result_type == "create"


@pytest.mark.asyncio
async def test_finance_query_interpretation_is_safe_inside_running_event_loop() -> None:
    plan = await interpret_finance_query(
        message="show me everything at Whole Foods last month",
        review_date="2026-03-31",
        finance_api=_StubFinanceQueryRead(),
        llm=_StubFinanceQueryLLM(...),
    )
    assert plan.intent == "list_transactions"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_goal_capture.py tests/test_finance_query_interpretation.py tests/test_finance_server.py -q`
Expected: FAIL with `RuntimeError` from nested `asyncio.run(...)`.

- [ ] **Step 3: Make interpretation entrypoints async and await them at tool boundaries**

```python
# minx_mcp/core/goal_capture.py
async def interpret_goal_capture_message(...) -> GoalCaptureResult | None:
    interpretation = await run_interpretation(...)
    ...


# minx_mcp/core/server.py
async def _goal_capture_async(...) -> dict[str, object]:
    ...
    result = await capture_goal_message_async(...)
```

```python
# minx_mcp/core/interpretation/finance_query.py
async def interpret_finance_query(...) -> FinanceQueryPlan:
    raw = await run_interpretation(...)
```

```python
# minx_mcp/finance/server.py
async def _finance_query_async(...) -> dict[str, object]:
    plan = await interpret_finance_query(...)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_goal_capture.py tests/test_finance_query_interpretation.py tests/test_finance_server.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/core/goal_capture.py minx_mcp/core/interpretation/finance_query.py minx_mcp/core/server.py minx_mcp/finance/server.py tests/test_goal_capture.py tests/test_finance_query_interpretation.py tests/test_finance_server.py
git commit -m "fix: make interpretation boundaries async-safe"
```

### Task 3: Tighten Finance Query Validation, Clarify Contracts, And Audit Coverage

**Files:**
- Modify: `minx_mcp/core/interpretation/models.py`
- Modify: `minx_mcp/core/models.py`
- Modify: `minx_mcp/finance/server.py`
- Modify: `minx_mcp/finance/analytics.py`
- Test: `tests/test_finance_query_interpretation.py`
- Test: `tests/test_finance_server.py`

- [ ] **Step 1: Write failing tests for invalid ranges, blank filters, malformed clarify payloads, and aggregate auditing**

```python
def test_sensitive_finance_query_rejects_reversed_date_range(tmp_path):
    server = create_finance_server(FinanceService(tmp_path / "minx.db", tmp_path / "vault"))
    sensitive = server._tool_manager.get_tool("sensitive_finance_query").fn

    assert sensitive(start_date="2026-03-31", end_date="2026-03-01") == {
        "success": False,
        "data": None,
        "error": "start_date must be on or before end_date",
        "error_code": "INVALID_INPUT",
    }


def test_sensitive_finance_query_rejects_blank_description_filter(tmp_path):
    server = create_finance_server(FinanceService(tmp_path / "minx.db", tmp_path / "vault"))
    sensitive = server._tool_manager.get_tool("sensitive_finance_query").fn

    assert sensitive(description_contains="   ")["error_code"] == "INVALID_INPUT"


def test_finance_query_interpretation_rejects_incomplete_clarify_payload() -> None:
    with pytest.raises(RuntimeError, match="schema"):
        asyncio.run(
            run_interpretation(
                llm=_StubFinanceQueryLLM('{"intent":"list_transactions","needs_clarification":true}'),
                prompt="test",
                result_model=FinanceQueryInterpretation,
            )
        )


def test_finance_query_sum_spending_is_audited(tmp_path):
    ...
    result = finance_query("how much did I spend at Whole Foods last month", "2026-03-31")
    row = service.conn.execute(
        "SELECT tool_name, detail FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["tool_name"] == "finance_query"
    assert "sum_spending" in row["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_finance_query_interpretation.py tests/test_finance_server.py -q`
Expected: FAIL because range validation, blank-filter validation, and aggregate audit logging are not complete.

- [ ] **Step 3: Implement strict validation and aggregate audit coverage**

```python
# minx_mcp/core/interpretation/models.py
class FinanceQueryInterpretation(BaseModel):
    ...

    @model_validator(mode="after")
    def _validate_clarify_consistency(self) -> "FinanceQueryInterpretation":
        if self.needs_clarification:
            if self.clarification_type is None or self.question is None:
                raise ValueError("clarify payload requires clarification_type and question")
        return self
```

```python
# minx_mcp/finance/server.py
def _validate_optional_text_filter(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise InvalidInputError(f"{name} must not be empty")
    return normalized


def _validate_optional_date_window(start_date: str | None, end_date: str | None) -> tuple[str | None, str | None]:
    ...
    if start is not None and end is not None and start > end:
        raise InvalidInputError("start_date must be on or before end_date")
    return start_date, end_date
```

```python
# minx_mcp/finance/analytics.py
from minx_mcp.audit import log_sensitive_access


def log_finance_query_access(conn: Connection, intent: str, session_ref: str | None, detail: str) -> None:
    log_sensitive_access(conn, "finance_query", session_ref, f"{intent}: {detail}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_query_interpretation.py tests/test_finance_server.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/core/interpretation/models.py minx_mcp/core/models.py minx_mcp/finance/server.py minx_mcp/finance/analytics.py tests/test_finance_query_interpretation.py tests/test_finance_server.py
git commit -m "fix: tighten finance query validation and audit coverage"
```

### Task 4: Add Shared Interpretation Context Builders And Redacted Logging

**Files:**
- Create: `minx_mcp/core/interpretation/context.py`
- Create: `minx_mcp/core/interpretation/logging.py`
- Modify: `minx_mcp/core/goal_capture.py`
- Modify: `minx_mcp/core/interpretation/finance_query.py`
- Modify: `minx_mcp/core/interpretation/runner.py`
- Test: `tests/test_interpretation_runner.py`
- Test: `tests/test_interpretation_logging.py`

- [ ] **Step 1: Write failing tests for compact context building and redacted logs**

```python
def test_build_finance_query_context_keeps_only_known_fields() -> None:
    context = build_finance_query_context(
        message="show me everything at Whole Foods last month",
        review_date="2026-03-31",
        category_names=["Groceries"],
        merchant_names=["Whole Foods"],
        account_names=["DCU"],
    )
    assert context["review_date"] == "2026-03-31"
    assert context["merchant_names"] == ["Whole Foods"]


def test_log_interpretation_failure_redacts_full_user_message(caplog) -> None:
    log_interpretation_failure(
        task="finance_query",
        prompt_summary="message_len=47 merchants=1 accounts=1",
        error=RuntimeError("schema failure"),
    )
    assert "schema failure" in caplog.text
    assert "show me everything" not in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_interpretation_runner.py tests/test_interpretation_logging.py -q`
Expected: FAIL because context/logging modules do not exist yet.

- [ ] **Step 3: Implement shared context builders and logging helpers**

```python
# minx_mcp/core/interpretation/context.py
def build_goal_capture_context(...) -> dict[str, object]:
    return {
        "message": message,
        "review_date": review_date,
        "active_goals": [...],
        "category_names": category_names[:50],
        "merchant_names": merchant_names[:50],
    }


def build_finance_query_context(...) -> dict[str, object]:
    return {
        "message": message,
        "review_date": review_date,
        "category_names": category_names[:100],
        "merchant_names": merchant_names[:100],
        "account_names": account_names[:20],
    }
```

```python
# minx_mcp/core/interpretation/logging.py
def log_interpretation_failure(*, task: str, prompt_summary: str, error: Exception) -> None:
    logger.warning("interpretation_failed task=%s summary=%s error=%s", task, prompt_summary, error)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_interpretation_runner.py tests/test_interpretation_logging.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/core/interpretation/context.py minx_mcp/core/interpretation/logging.py minx_mcp/core/goal_capture.py minx_mcp/core/interpretation/finance_query.py minx_mcp/core/interpretation/runner.py tests/test_interpretation_runner.py tests/test_interpretation_logging.py
git commit -m "feat: add shared interpretation context and logging"
```

## Phase 2: Finance Maturity

### Task 5: Add Merchant Normalization And Alias Resolution

**Files:**
- Create: `minx_mcp/finance/normalization.py`
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/finance/import_workflow.py`
- Modify: `minx_mcp/finance/analytics.py`
- Test: `tests/test_finance_rules.py`
- Test: `tests/test_finance_service.py`

- [ ] **Step 1: Write the failing normalization tests**

```python
def test_normalize_merchant_collapses_variants() -> None:
    assert normalize_merchant("SQ *JOES CAFE 1234") == "Joe's Cafe"
    assert normalize_merchant("JOES CAFE AUSTIN") == "Joe's Cafe"


def test_import_persists_raw_and_canonical_merchant(tmp_path):
    ...
    tx = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx["merchant"] == "Joe's Cafe"
    assert tx["raw_merchant"] == "SQ *JOES CAFE 1234"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_finance_rules.py tests/test_finance_service.py -q`
Expected: FAIL because merchant normalization does not exist yet.

- [ ] **Step 3: Implement canonical merchant normalization**

```python
# minx_mcp/finance/normalization.py
def normalize_merchant(raw_merchant: str | None) -> str | None:
    if raw_merchant is None:
        return None
    stripped = raw_merchant.strip().upper()
    stripped = re.sub(r"^SQ \*", "", stripped)
    stripped = re.sub(r"\s+#?\d+$", "", stripped)
    canonical = stripped.title()
    return _KNOWN_CANONICAL_ALIASES.get(canonical, canonical)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_rules.py tests/test_finance_service.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/finance/normalization.py minx_mcp/finance/service.py minx_mcp/finance/import_workflow.py minx_mcp/finance/analytics.py tests/test_finance_rules.py tests/test_finance_service.py
git commit -m "feat: add merchant normalization"
```

### Task 6: Add Staged Finance Rules

**Files:**
- Create: `minx_mcp/finance/rules.py`
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/finance/import_workflow.py`
- Test: `tests/test_finance_rules.py`
- Test: `tests/test_finance_service.py`

- [ ] **Step 1: Write the failing staged-rule tests**

```python
def test_staged_rules_apply_in_priority_order() -> None:
    txn = {"merchant": "Joe's Cafe", "category_name": None}
    rules = [
        Rule(stage="normalize", priority=10, kind="rename_merchant", match="JOES CAFE", value="Joe's Cafe"),
        Rule(stage="categorize", priority=20, kind="categorize_merchant", match="Joe's Cafe", value="Dining Out"),
    ]
    result = apply_rules(txn, rules)
    assert result["merchant"] == "Joe's Cafe"
    assert result["category_name"] == "Dining Out"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_finance_rules.py tests/test_finance_service.py -q`
Expected: FAIL because staged rules do not exist yet.

- [ ] **Step 3: Implement deterministic staged-rule evaluation**

```python
# minx_mcp/finance/rules.py
@dataclass(frozen=True)
class Rule:
    stage: Literal["normalize", "categorize", "finalize"]
    priority: int
    kind: str
    match: str
    value: str


def apply_rules(txn: dict[str, object], rules: list[Rule]) -> dict[str, object]:
    current = dict(txn)
    for rule in sorted(rules, key=lambda r: (_STAGE_ORDER[r.stage], r.priority)):
        current = _apply_rule(current, rule)
    return current
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_rules.py tests/test_finance_service.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/finance/rules.py minx_mcp/finance/service.py minx_mcp/finance/import_workflow.py tests/test_finance_rules.py tests/test_finance_service.py
git commit -m "feat: add staged finance rules"
```

### Task 7: Add Import Preview And Clarification Surface

**Files:**
- Modify: `minx_mcp/finance/server.py`
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/finance/import_workflow.py`
- Test: `tests/test_finance_import_preview.py`
- Test: `tests/test_finance_server.py`

- [ ] **Step 1: Write the failing preview tests**

```python
def test_finance_import_preview_returns_detected_mapping_and_sample(tmp_path):
    ...
    result = finance_import_preview(str(source), "DCU")
    assert result["preview"]["source_kind"] == "dcu_csv"
    assert result["preview"]["sample_transactions"][0]["description"] == "H-E-B"


def test_finance_import_preview_reports_mapping_clarify_for_unknown_generic_csv(tmp_path):
    ...
    assert result["preview"]["result_type"] == "clarify"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_finance_import_preview.py tests/test_finance_server.py -q`
Expected: FAIL because preview tool and service path do not exist yet.

- [ ] **Step 3: Implement detect -> map -> preview -> import**

```python
# minx_mcp/finance/import_workflow.py
def preview_finance_import(... ) -> dict[str, object]:
    parsed = parse_source_file(...)
    return {
        "result_type": "preview",
        "source_kind": effective_source_kind,
        "sample_transactions": [_sample_txn(txn) for txn in parsed.transactions[:10]],
        "warnings": warnings,
    }
```

```python
# minx_mcp/finance/server.py
@mcp.tool(name="finance_import_preview")
def finance_import_preview(source_ref: str, account_name: str, source_kind: str | None = None) -> dict[str, object]:
    return wrap_tool_call(lambda: _finance_import_preview(service, source_ref, account_name, source_kind))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_import_preview.py tests/test_finance_server.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/finance/server.py minx_mcp/finance/service.py minx_mcp/finance/import_workflow.py tests/test_finance_import_preview.py tests/test_finance_server.py
git commit -m "feat: add finance import preview"
```

### Task 8: Add Richer Finance Monitoring Views

**Files:**
- Modify: `minx_mcp/finance/analytics.py`
- Modify: `minx_mcp/finance/server.py`
- Modify: `minx_mcp/finance/service.py`
- Test: `tests/test_finance_monitoring.py`

- [ ] **Step 1: Write the failing monitoring tests**

```python
def test_finance_monitoring_reports_category_rollups_and_recurring_income(tmp_path):
    ...
    result = service.finance_monitoring(period_start="2026-03-01", period_end="2026-03-31")
    assert result["top_categories"][0]["category_name"] == "Groceries"
    assert result["income_patterns"][0]["merchant"] == "Employer"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_finance_monitoring.py -q`
Expected: FAIL because monitoring read models do not exist yet.

- [ ] **Step 3: Implement category/merchant/income monitoring read models**

```python
# minx_mcp/finance/analytics.py
def build_finance_monitoring(... ) -> dict[str, object]:
    return {
        "top_categories": [...],
        "top_merchants": [...],
        "income_patterns": [...],
        "uncategorized_summary": {...},
        "changes_vs_prior_period": [...],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_monitoring.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/finance/analytics.py minx_mcp/finance/server.py minx_mcp/finance/service.py tests/test_finance_monitoring.py
git commit -m "feat: add finance monitoring views"
```

## Phase 3: Goal-Finance Intelligence

### Task 9: Add Goal-Supporting Finance Insights

**Files:**
- Modify: `minx_mcp/core/review.py`
- Modify: `minx_mcp/core/read_models.py`
- Modify: `minx_mcp/core/goal_detectors.py`
- Modify: `minx_mcp/finance/read_api.py`
- Test: `tests/test_goal_finance_insights.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing goal-insight tests**

```python
def test_goal_finance_insight_flags_monthly_spending_cap_risk(tmp_path):
    ...
    review = generate_daily_review(...)
    assert any("68% of your monthly cap" in insight.summary for insight in review.insights)


def test_goal_finance_insight_flags_missing_savings_transfer_after_paycheck(tmp_path):
    ...
    assert any("paycheck landed" in insight.summary for insight in review.insights)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_goal_finance_insights.py tests/test_review.py -q`
Expected: FAIL because goal-supporting finance insights do not exist yet.

- [ ] **Step 3: Implement pacing/risk/progress insight generation**

```python
# minx_mcp/core/goal_detectors.py
def detect_goal_finance_risks(... ) -> list[InsightCandidate]:
    return [
        _monthly_cap_pacing_insight(...),
        _income_followthrough_insight(...),
        _merchant_spike_goal_risk_insight(...),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_goal_finance_insights.py tests/test_review.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/core/review.py minx_mcp/core/read_models.py minx_mcp/core/goal_detectors.py minx_mcp/finance/read_api.py tests/test_goal_finance_insights.py tests/test_review.py
git commit -m "feat: add goal-supporting finance insights"
```

## Verification Batch

- [ ] **Step 1: Run the focused hardening suites**

Run: `uv run python -m pytest tests/test_core_server.py tests/test_goal_capture.py tests/test_interpretation_runner.py tests/test_interpretation_logging.py tests/test_finance_query_interpretation.py tests/test_finance_server.py tests/test_finance_service.py tests/test_finance_parsers.py tests/test_finance_import_preview.py tests/test_finance_rules.py tests/test_finance_monitoring.py tests/test_goal_finance_insights.py -q`
Expected: PASS

- [ ] **Step 2: Run the full repo suite**

Run: `uv run python -m pytest tests -q`
Expected: PASS

- [ ] **Step 3: Run type-checking**

Run: `uv run python -m mypy`
Expected: `Success: no issues found ...`

- [ ] **Step 4: Update handoff**

```markdown
- record which phases/tasks shipped
- record latest verification counts
- record remaining deferrals honestly
```

- [ ] **Step 5: Commit the completed verification and handoff update**

```bash
git add HANDOFF.md
git commit -m "docs: update handoff after current-domain hardening"
```
