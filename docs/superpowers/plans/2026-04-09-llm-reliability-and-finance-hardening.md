# Minx LLM Reliability + Finance Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared low-cost LLM interpretation layer, use it for goal capture/import detection/natural-language finance reads, and harden the finance domain with the highest-value deterministic cleanup from the approved spec.

**Architecture:** The implementation keeps Minx's current split intact: Core owns interpretation, Finance owns facts, and deterministic code remains responsible for validation, calculations, policy, and persistence. Phase 1 builds the interpretation foundation plus direct reliability wins; Phase 2 layers in finance-maturity improvements inspired by Actual, actual-mcp, Firefly, and beangulp without turning the repo into an LLM-heavy system.

**Tech Stack:** Python 3.12, SQLite, FastMCP, Pydantic, pytest, mypy, existing `openai_compatible` LLM path

---

## File Structure

### New files

- `minx_mcp/core/interpretation/__init__.py`
  Responsibility: package boundary for shared interpretation utilities.
- `minx_mcp/core/interpretation/models.py`
  Responsibility: typed interpretation request/result models for goal capture, finance query, and import detection.
- `minx_mcp/core/interpretation/runner.py`
  Responsibility: common LLM invocation, schema parsing, timeout handling, and structured-result normalization.
- `minx_mcp/core/interpretation/context.py`
  Responsibility: compact context builders shared across interpretation tasks.
- `minx_mcp/core/interpretation/goal_capture.py`
  Responsibility: LLM-backed goal capture orchestration plus deterministic validation/adaptation into existing `GoalCaptureResult`.
- `minx_mcp/core/interpretation/finance_query.py`
  Responsibility: natural-language finance query translation into a typed deterministic query plan.
- `minx_mcp/core/interpretation/import_detection.py`
  Responsibility: import/source-kind detection over sampled file evidence.
- `tests/test_interpretation_runner.py`
  Responsibility: runner/schema/fallback tests.
- `tests/test_finance_query_interpretation.py`
  Responsibility: natural-language finance query plan tests and finance server integration coverage.

### Modified files

- `minx_mcp/core/server.py`
  Responsibility: route `goal_capture` through the shared interpretation layer.
- `minx_mcp/core/goal_capture.py`
  Responsibility: shrink to compatibility helpers or retire logic moved into `core/interpretation/goal_capture.py`.
- `minx_mcp/core/models.py`
  Responsibility: shared typed models used by Core plus any new query-plan/result dataclasses needed by interpretation and finance reads.
- `minx_mcp/core/llm.py`
  Responsibility: expose a reusable low-cost JSON-backed model invocation path for interpretation tasks.
- `minx_mcp/finance/analytics.py`
  Responsibility: add filtered deterministic sensitive-query execution and move anomaly threshold to preferences.
- `minx_mcp/finance/server.py`
  Responsibility: expose richer filtered read tools and an NL finance query tool boundary.
- `minx_mcp/finance/service.py`
  Responsibility: category-hint wiring, explicit filtered read support, and any small service cleanups needed by the new query path.
- `minx_mcp/finance/importers.py`
  Responsibility: replace filename-only detection with staged detection using sampled file evidence.
- `minx_mcp/preferences.py`
  Responsibility: support anomaly-threshold preference reads if a helper is useful.
- `tests/test_goal_capture.py`
  Responsibility: retain contract tests while changing the underlying implementation.
- `tests/test_core_server.py`
  Responsibility: end-to-end `goal_capture` and protected review coverage after the rewrite.
- `tests/test_finance_parsers.py`
  Responsibility: update import detection coverage for content-based detection.
- `tests/test_finance_server.py`
  Responsibility: filtered read and NL finance query tool coverage.
- `tests/test_finance_service.py`
  Responsibility: category-hint wiring and filtered-read behavior tests.

### Phase 2 likely additions

- `minx_mcp/finance/rules.py`
  Responsibility: staged rule evaluation inspired by Actual's ordered rules.
- `minx_mcp/finance/normalization.py`
  Responsibility: merchant alias/canonicalization helpers.
- `tests/test_finance_rules.py`
  Responsibility: staged rule evaluation tests.

## Task 1: Shared Interpretation Foundation

**Files:**
- Create: `minx_mcp/core/interpretation/__init__.py`
- Create: `minx_mcp/core/interpretation/models.py`
- Create: `minx_mcp/core/interpretation/runner.py`
- Create: `minx_mcp/core/interpretation/context.py`
- Modify: `minx_mcp/core/llm.py`
- Test: `tests/test_interpretation_runner.py`

- [ ] **Step 1: Write the failing tests**

```python
from minx_mcp.core.interpretation.models import GoalCaptureInterpretation
from minx_mcp.core.interpretation.runner import run_interpretation


class _StubLLM:
    async def run_json_prompt(self, prompt: str) -> str:
        return '{"intent":"create","confidence":0.91}'


async def test_run_interpretation_parses_typed_json_result() -> None:
    result = await run_interpretation(
        llm=_StubLLM(),
        prompt="test",
        result_model=GoalCaptureInterpretation,
    )
    assert result.intent == "create"
    assert result.confidence == 0.91


class _BadLLM:
    async def run_json_prompt(self, prompt: str) -> str:
        return '{"intent":"unknown"}'


async def test_run_interpretation_raises_on_schema_mismatch() -> None:
    try:
        await run_interpretation(
            llm=_BadLLM(),
            prompt="test",
            result_model=GoalCaptureInterpretation,
        )
    except RuntimeError as exc:
        assert "schema" in str(exc).lower()
    else:
        raise AssertionError("Expected schema failure")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_interpretation_runner.py -q`
Expected: FAIL because the interpretation package and runner do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# minx_mcp/core/interpretation/models.py
from pydantic import BaseModel


class GoalCaptureInterpretation(BaseModel):
    intent: str
    confidence: float


# minx_mcp/core/interpretation/runner.py
import json
from pydantic import ValidationError


async def run_interpretation(*, llm, prompt: str, result_model):
    payload = await llm.run_json_prompt(prompt)
    try:
        data = json.loads(payload)
        return result_model.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError("Interpretation schema validation failed") from exc
```

- [ ] **Step 4: Extend the shared LLM path for reuse**

```python
# minx_mcp/core/llm.py
class JSONPromptLLM(Protocol):
    async def run_json_prompt(self, prompt: str) -> str: ...


class OpenAICompatibleLLM:
    async def run_json_prompt(self, prompt: str) -> str:
        response = await self._post_chat_completion(prompt)
        return extract_openai_message_content(response)
```

Keep this change narrow: reuse the current `openai_compatible` client path rather than inventing a second provider stack.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_interpretation_runner.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/core/interpretation minx_mcp/core/llm.py tests/test_interpretation_runner.py
git commit -m "feat: add shared interpretation runner"
```

## Task 2: Rewrite Goal Capture On The Shared Interpretation Layer

**Files:**
- Create: `minx_mcp/core/interpretation/goal_capture.py`
- Modify: `minx_mcp/core/server.py`
- Modify: `minx_mcp/core/goal_capture.py`
- Modify: `minx_mcp/core/models.py`
- Test: `tests/test_goal_capture.py`
- Test: `tests/test_core_server.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_capture_goal_message_handles_natural_language_without_regex_trigger_words() -> None:
    result = capture_goal_message(
        message="I want to track my Amazon spending under $200 monthly",
        review_date="2026-03-15",
        finance_api=finance_api,
        goals=[],
    )
    assert result.result_type == "create"
    assert result.payload["merchant_names"] == ["Amazon"]


def test_capture_goal_message_returns_clarify_when_interpretation_is_ambiguous() -> None:
    result = capture_goal_message(
        message="lower my target goal",
        review_date="2026-03-15",
        finance_api=finance_api,
        goals=goals,
    )
    assert result.result_type == "clarify"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_goal_capture.py tests/test_core_server.py -q`
Expected: FAIL on unsupported natural-language cases.

- [ ] **Step 3: Implement the interpretation adapter**

```python
# minx_mcp/core/interpretation/goal_capture.py
async def interpret_goal_capture(
    *,
    llm,
    message: str,
    review_date: str,
    finance_api,
    goals: list[GoalRecord],
) -> GoalCaptureResult:
    context = build_goal_capture_context(
        message=message,
        review_date=review_date,
        goals=goals,
        category_names=finance_api.list_goal_category_names(),
        merchant_names=finance_api.list_spending_merchant_names(),
    )
    raw = await run_interpretation(
        llm=llm,
        prompt=render_goal_capture_prompt(context),
        result_model=GoalCaptureInterpretation,
    )
    return validate_goal_capture_interpretation(raw, finance_api=finance_api, goals=goals)
```

- [ ] **Step 4: Preserve the existing external contract**

```python
# minx_mcp/core/server.py
def _goal_capture(...):
    ...
    result = capture_goal_message(
        message=normalized_message,
        review_date=effective_review_date,
        finance_api=FinanceReadAPI(conn),
        goals=goals,
        llm=create_llm(db_path=config.db_path),
    )
    return _goal_capture_result_to_dict(result)
```

Keep `GoalCaptureResult` as the outward-facing contract so existing tests and downstream expectations survive.

- [ ] **Step 5: Add deterministic fallback behavior**

```python
def capture_goal_message(..., llm=None) -> GoalCaptureResult:
    if llm is None:
        return _legacy_capture_goal_message(...)
    try:
        return asyncio.run(interpret_goal_capture(...))
    except Exception:
        return _legacy_capture_goal_message(...)
```

If a synchronous helper is cleaner, use it. The point is to keep a non-LLM fallback path for malformed/failed model output.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_goal_capture.py tests/test_core_server.py tests/test_core_mcp_stdio.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add minx_mcp/core/interpretation/goal_capture.py minx_mcp/core/server.py minx_mcp/core/goal_capture.py minx_mcp/core/models.py tests/test_goal_capture.py tests/test_core_server.py tests/test_core_mcp_stdio.py
git commit -m "feat: add llm-backed goal capture"
```

## Task 3: Add Deterministic Filtered Finance Reads

**Files:**
- Modify: `minx_mcp/finance/analytics.py`
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/finance/server.py`
- Test: `tests/test_finance_server.py`
- Test: `tests/test_finance_service.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_sensitive_finance_query_filters_by_merchant_and_date(service) -> None:
    result = service.sensitive_finance_query(
        limit=50,
        merchant="Whole Foods",
        start_date="2026-03-01",
        end_date="2026-03-31",
    )
    assert all(txn["merchant"] == "Whole Foods" for txn in result["transactions"])


def test_sensitive_finance_query_filters_by_description_contains(server_tool) -> None:
    result = server_tool(
        limit=50,
        description_contains="refund",
    )
    assert result["success"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_finance_server.py tests/test_finance_service.py -q`
Expected: FAIL because the filtered arguments are not supported yet.

- [ ] **Step 3: Implement the deterministic filtered query builder**

```python
# minx_mcp/finance/analytics.py
def sensitive_query(
    conn: Connection,
    *,
    limit: int = 50,
    session_ref: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    category_name: str | None = None,
    merchant: str | None = None,
    account_name: str | None = None,
    description_contains: str | None = None,
) -> dict[str, object]:
    clauses = []
    params = []
    ...
```

- [ ] **Step 4: Thread the arguments through service and server**

```python
# minx_mcp/finance/service.py
def sensitive_finance_query(..., merchant: str | None = None, ...) -> dict[str, object]:
    return sensitive_query(self.conn, limit=limit, session_ref=session_ref, merchant=merchant, ...)
```

```python
# minx_mcp/finance/server.py
def sensitive_finance_query(
    limit: int = 50,
    session_ref: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    category_name: str | None = None,
    merchant: str | None = None,
    account_name: str | None = None,
    description_contains: str | None = None,
) -> dict[str, object]:
    ...
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_server.py tests/test_finance_service.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/finance/analytics.py minx_mcp/finance/service.py minx_mcp/finance/server.py tests/test_finance_server.py tests/test_finance_service.py
git commit -m "feat: add filtered finance detail queries"
```

## Task 4: Add Natural-Language Finance Query Translation

**Files:**
- Create: `minx_mcp/core/interpretation/finance_query.py`
- Modify: `minx_mcp/finance/server.py`
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/core/models.py`
- Test: `tests/test_finance_query_interpretation.py`
- Test: `tests/test_finance_server.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_finance_query_interpretation_resolves_sum_spending_request() -> None:
    plan = interpret_finance_query(
        message="how much did I spend on restaurants this week",
        review_date="2026-03-15",
        finance_api=finance_api,
        llm=stub_llm,
    )
    assert plan.intent == "sum_spending"
    assert plan.filters.category_name == "Restaurants"


def test_finance_nl_query_tool_executes_validated_query_plan(server_tool) -> None:
    result = server_tool("show me everything at Whole Foods last month", "2026-03-31")
    assert result["success"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_finance_query_interpretation.py tests/test_finance_server.py -q`
Expected: FAIL because the interpretation file and tool do not exist yet.

- [ ] **Step 3: Implement a typed query-plan interpreter**

```python
# minx_mcp/core/interpretation/finance_query.py
def interpret_finance_query(... ) -> FinanceQueryPlan:
    context = build_finance_query_context(...)
    raw = ...
    return validate_finance_query_plan(raw, finance_api=finance_api)
```

- [ ] **Step 4: Add a new MCP-facing tool**

```python
# minx_mcp/finance/server.py
@mcp.tool(name="finance_query")
def finance_query(message: str, review_date: str | None = None) -> dict[str, object]:
    return wrap_tool_call(lambda: _finance_query(service, message, review_date))
```

The tool should return:

- the executed deterministic result when the plan is valid and unambiguous
- a clarify contract when the plan is ambiguous

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_query_interpretation.py tests/test_finance_server.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/core/interpretation/finance_query.py minx_mcp/finance/server.py minx_mcp/finance/service.py minx_mcp/core/models.py tests/test_finance_query_interpretation.py tests/test_finance_server.py
git commit -m "feat: add natural language finance queries"
```

## Task 5: Replace Filename-Only Import Detection

**Files:**
- Create: `minx_mcp/core/interpretation/import_detection.py`
- Modify: `minx_mcp/finance/importers.py`
- Modify: `minx_mcp/finance/import_workflow.py`
- Test: `tests/test_finance_parsers.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_detect_source_kind_uses_csv_headers_not_just_filename(tmp_path: Path) -> None:
    path = tmp_path / "bank_march.csv"
    path.write_text("Date,Description,Amount\n2026-03-01,Coffee,-4.50\n")
    assert detect_source_kind(path, sample_text=path.read_text()) == "dcu_csv"


def test_detect_source_kind_returns_clarifyable_error_when_unknown(tmp_path: Path) -> None:
    path = tmp_path / "mystery.csv"
    path.write_text("foo,bar,baz\n1,2,3\n")
    try:
        detect_source_kind(path, sample_text=path.read_text())
    except InvalidInputError as exc:
        assert "columns" in str(exc).lower()
    else:
        raise AssertionError("Expected detection failure")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_finance_parsers.py -q`
Expected: FAIL because detection is filename-only.

- [ ] **Step 3: Implement staged detection**

```python
# minx_mcp/finance/importers.py
def detect_source_kind(path: Path, *, sample_text: str | None = None, llm=None) -> str:
    deterministic = _detect_source_kind_from_filename(path)
    if deterministic is not None:
        return deterministic
    sampled = sample_text or _sample_file_for_detection(path)
    detection = interpret_import_source(
        path=path,
        sample_text=sampled,
        llm=llm,
    )
    return validate_detected_source_kind(detection)
```

- [ ] **Step 4: Thread the configured LLM into import workflow**

```python
# minx_mcp/finance/import_workflow.py
effective_source_kind = source_kind or detect_source_kind(
    canonical_source_path,
    llm=create_llm(db_path=host.conn_db_path),
)
```

If `host` cannot expose `db_path` cleanly, add the smallest host-surface extension needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_parsers.py tests/test_finance_service.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/core/interpretation/import_detection.py minx_mcp/finance/importers.py minx_mcp/finance/import_workflow.py tests/test_finance_parsers.py tests/test_finance_service.py
git commit -m "feat: add content-based import detection"
```

## Task 6: Wire Category Hints And Preference-Backed Anomaly Thresholds

**Files:**
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/finance/analytics.py`
- Modify: `minx_mcp/preferences.py`
- Test: `tests/test_finance_service.py`
- Test: `tests/test_finance_server.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_insert_transaction_uses_matching_category_hint(service) -> None:
    txn = ParsedTransaction(
        posted_at="2026-03-01",
        description="HEB",
        merchant="HEB",
        amount_cents=-4200,
        category_hint="groceries",
        external_id="x1",
    )
    transaction_id = service._insert_transaction(account_id, batch_id, txn)
    row = service.conn.execute(
        "SELECT c.name FROM finance_transactions t JOIN finance_categories c ON c.id = t.category_id WHERE t.id = ?",
        (transaction_id,),
    ).fetchone()
    assert row["name"] == "Groceries"


def test_find_anomalies_reads_threshold_from_preferences(conn) -> None:
    set_preference(conn, "finance", "anomaly_threshold_cents", -5000)
    items = find_anomalies(conn)
    assert items
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_finance_service.py tests/test_finance_server.py -q`
Expected: FAIL because hints are ignored and threshold is hardcoded.

- [ ] **Step 3: Implement minimal deterministic hint wiring**

```python
# minx_mcp/finance/service.py
matched_category_id = self._match_category_hint(txn.category_hint)
category_id = matched_category_id or self._uncategorized_id()
category_source = "hint" if matched_category_id is not None else "uncategorized"
```

- [ ] **Step 4: Read threshold from preferences**

```python
# minx_mcp/finance/analytics.py
from minx_mcp.preferences import get_preference


def _anomaly_threshold(conn: Connection) -> int:
    value = get_preference(conn, "finance", "anomaly_threshold_cents", -25_000)
    return int(value)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_service.py tests/test_finance_server.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/finance/service.py minx_mcp/finance/analytics.py minx_mcp/preferences.py tests/test_finance_service.py tests/test_finance_server.py
git commit -m "feat: use category hints and configurable anomaly thresholds"
```

## Task 7: Phase 2 Finance Rules And Merchant Normalization

**Files:**
- Create: `minx_mcp/finance/rules.py`
- Create: `minx_mcp/finance/normalization.py`
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/finance/import_workflow.py`
- Test: `tests/test_finance_rules.py`
- Test: `tests/test_finance_service.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_staged_rules_apply_in_priority_order() -> None:
    result = apply_rules(
        txn={"merchant": "WHOLEFDS #123", "description": "WHOLEFDS #123"},
        rules=[
            Rule(stage="pre", action="normalize_merchant", pattern="WHOLEFDS", value="Whole Foods"),
            Rule(stage="default", action="categorize", pattern="Whole Foods", value="Groceries"),
        ],
    )
    assert result.merchant == "Whole Foods"
    assert result.category_name == "Groceries"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_finance_rules.py -q`
Expected: FAIL because staged rules and normalization do not exist.

- [ ] **Step 3: Implement minimal staged-rule engine**

```python
@dataclass(frozen=True)
class Rule:
    stage: str
    action: str
    pattern: str
    value: str


def apply_rules(txn: dict[str, str], rules: list[Rule]) -> RuleResult:
    ...
```

- [ ] **Step 4: Use staged normalization during import**

```python
# minx_mcp/finance/import_workflow.py
normalized_txn = normalize_transaction(txn)
ruled_txn = apply_rules_to_transaction(normalized_txn, rules)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_rules.py tests/test_finance_service.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/finance/rules.py minx_mcp/finance/normalization.py minx_mcp/finance/service.py minx_mcp/finance/import_workflow.py tests/test_finance_rules.py tests/test_finance_service.py
git commit -m "feat: add staged finance rules and merchant normalization"
```

## Task 8: Phase 2 Import Preview, Audit Surfacing, And Observability

**Files:**
- Modify: `minx_mcp/finance/server.py`
- Modify: `minx_mcp/finance/service.py`
- Modify: `minx_mcp/audit.py`
- Modify: `minx_mcp/core/interpretation/runner.py`
- Test: `tests/test_finance_server.py`
- Test: `tests/test_audit.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_finance_import_preview_returns_detected_plan_without_mutation(server_tool) -> None:
    result = server_tool(source_ref="...", account_name="Checking", preview=True)
    assert result["success"] is True
    assert result["data"]["preview"] is True


def test_finance_audit_summary_returns_aggregate_counts(conn) -> None:
    log_sensitive_access(conn, "sensitive_finance_query", "abc", "Returned 2 rows")
    summary = audit_summary(conn)
    assert summary["total_queries"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_finance_server.py tests/test_audit.py -q`
Expected: FAIL because preview and audit summary do not exist.

- [ ] **Step 3: Implement audit summary and preview mode**

```python
def audit_summary(conn: Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS total_queries FROM audit_log WHERE tool_name = 'sensitive_finance_query'"
    ).fetchone()
    return {"total_queries": int(row["total_queries"])}
```

```python
def finance_import(..., preview: bool = False) -> dict[str, object]:
    if preview:
        return build_import_preview(...)
    return run_finance_import(...)
```

- [ ] **Step 4: Add lightweight interpretation observability**

```python
# minx_mcp/core/interpretation/runner.py
logger.info(
    "interpretation task=%s success=%s duration_ms=%s",
    task_name,
    True,
    duration_ms,
)
```

Keep this to logging and counts. Do not add a heavyweight metrics framework.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_finance_server.py tests/test_audit.py tests/test_interpretation_runner.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/finance/server.py minx_mcp/finance/service.py minx_mcp/audit.py minx_mcp/core/interpretation/runner.py tests/test_finance_server.py tests/test_audit.py tests/test_interpretation_runner.py
git commit -m "feat: add import preview audit summary and interpretation logging"
```

## Final Verification

- [ ] **Step 1: Run focused Phase 1 verification**

Run: `uv run python -m pytest tests/test_interpretation_runner.py tests/test_goal_capture.py tests/test_core_server.py tests/test_core_mcp_stdio.py tests/test_finance_server.py tests/test_finance_service.py tests/test_finance_parsers.py tests/test_finance_query_interpretation.py -q`
Expected: PASS

- [ ] **Step 2: Run full test suite**

Run: `uv run python -m pytest tests -q`
Expected: PASS

- [ ] **Step 3: Run type checking**

Run: `uv run python -m mypy`
Expected: `Success: no issues found in ... source files`

- [ ] **Step 4: Update docs if behavior changed materially**

```bash
git add README.md HANDOFF.md docs/superpowers/specs/2026-04-09-llm-reliability-and-finance-hardening-design.md docs/superpowers/plans/2026-04-09-llm-reliability-and-finance-hardening.md
git commit -m "docs: refresh llm reliability and finance hardening docs"
```
