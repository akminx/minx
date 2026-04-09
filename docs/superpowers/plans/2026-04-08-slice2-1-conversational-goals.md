# Slice 2.1 Phase B Conversational Goals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic `goal_capture` MCP tool that converts narrow finance-first conversational goal language into explicit `goal_create` or `goal_update` proposals, plus clarifications and `no_match` results.

**Architecture:** Keep the natural-language policy in a small Core interpreter module and keep mutation ownership in the existing structured goal tools. Extend the finance read boundary just enough to expose deterministic category and merchant candidate lists, then wire the interpreter into the MCP server and prove the flow with unit, contract, stdio, and end-to-end tests.

**Tech Stack:** Python 3.12, dataclasses, Protocols, FastMCP, pytest, existing Minx Core goals/review pipeline, SQLite-backed FinanceReadAPI

---

## File Structure

**Create**

- `minx_mcp/core/goal_capture.py`
  Deterministic parsing, normalization, subject resolution, update matching, and typed result builders for conversational goal capture.
- `tests/test_goal_capture.py`
  Unit tests for create, update, clarify, and `no_match` behavior without MCP wiring.

**Modify**

- `minx_mcp/core/models.py`
  Extend `FinanceReadInterface` with deterministic subject-list methods and add typed dataclasses for `goal_capture` results/options.
- `minx_mcp/finance/read_api.py`
  Implement `list_goal_category_names()` and `list_spending_merchant_names()` with expense-aware deterministic ordering.
- `minx_mcp/core/server.py`
  Register `goal_capture`, validate inputs, construct `ReviewContext`-style dependencies, and return contract-wrapped capture results.
- `tests/test_finance_read_api.py`
  Add deterministic read-boundary tests for eligible categories and merchant lists.
- `tests/test_core_server.py`
  Add MCP-boundary tests for `goal_capture` registration, validation, and contract responses.
- `tests/test_core_mcp_stdio.py`
  Extend stdio coverage to include `goal_capture` and one create/update flow through the new tool.

---

### Task 1: Extend The Finance Read Boundary For Deterministic Subject Discovery

**Files:**
- Modify: `minx_mcp/core/models.py`
- Modify: `minx_mcp/finance/read_api.py`
- Test: `tests/test_finance_read_api.py`

- [ ] **Step 1: Write failing finance read API tests for category and merchant discovery**

```python
def test_list_goal_category_names_returns_sorted_spend_eligible_categories_without_income(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    from minx_mcp.finance.read_api import FinanceReadAPI

    categories = FinanceReadAPI(conn).list_goal_category_names()

    assert categories == [
        "Dining Out",
        "Groceries",
        "Shopping",
        "Subscriptions",
        "Transportation",
        "Uncategorized",
    ]


def test_list_spending_merchant_names_returns_distinct_sorted_nonblank_expense_merchants(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-01",
        description="Lunch",
        merchant="Cafe",
        amount_cents=-1200,
        category_id=3,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-02",
        description="Lunch again",
        merchant="Cafe",
        amount_cents=-800,
        category_id=3,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-03",
        description="Paycheck",
        merchant="Employer",
        amount_cents=250000,
        category_id=4,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-04",
        description="Blank merchant",
        merchant="",
        amount_cents=-500,
        category_id=6,
    )
    conn.commit()

    from minx_mcp.finance.read_api import FinanceReadAPI

    merchants = FinanceReadAPI(conn).list_spending_merchant_names()

    assert merchants == ["Cafe"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_finance_read_api.py -k "list_goal_category_names or list_spending_merchant_names" -q`
Expected: FAIL with `AttributeError` because the methods do not exist yet.

- [ ] **Step 3: Extend the read protocol and implement the new methods**

```python
class FinanceReadInterface(Protocol):
    def get_spending_summary(self, start_date: str, end_date: str): ...
    def get_uncategorized(self, start_date: str, end_date: str): ...
    def get_import_job_issues(self): ...
    def get_period_comparison(
        self,
        current_start: str,
        current_end: str,
        prior_start: str,
        prior_end: str,
    ): ...
    def list_goal_category_names(self) -> list[str]: ...
    def list_spending_merchant_names(self) -> list[str]: ...
    def get_filtered_spending_total(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int: ...
```

```python
def list_goal_category_names(self) -> list[str]:
    rows = self._db.execute(
        """
        SELECT name
        FROM finance_categories
        WHERE name != 'Income'
        ORDER BY name ASC
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def list_spending_merchant_names(self) -> list[str]:
    rows = self._db.execute(
        """
        SELECT DISTINCT merchant
        FROM finance_transactions
        WHERE amount_cents < 0
          AND COALESCE(merchant, '') != ''
        ORDER BY merchant ASC
        """
    ).fetchall()
    return [str(row["merchant"]) for row in rows]
```

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_finance_read_api.py -k "list_goal_category_names or list_spending_merchant_names" -q`
Expected: PASS with both new tests green.

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/core/models.py minx_mcp/finance/read_api.py tests/test_finance_read_api.py
git commit -m "feat: add finance subject discovery for goal capture"
```

---

### Task 2: Build The Deterministic Goal Capture Interpreter With Unit Tests

**Files:**
- Create: `minx_mcp/core/goal_capture.py`
- Modify: `minx_mcp/core/models.py`
- Test: `tests/test_goal_capture.py`

- [ ] **Step 1: Write failing interpreter tests for the supported result types**

```python
from minx_mcp.core.goal_capture import capture_goal_message
from minx_mcp.core.models import GoalRecord


class _StubFinanceRead:
    def list_goal_category_names(self) -> list[str]:
        return ["Dining Out", "Groceries", "Shopping", "Uncategorized"]

    def list_spending_merchant_names(self) -> list[str]:
        return ["Cafe", "Netflix"]


def test_capture_goal_message_builds_create_payload_for_category_goal() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "create"
    assert result.action == "goal_create"
    assert result.payload["title"] == "Dining Out Spending Cap"
    assert result.payload["target_value"] == 25_000
    assert result.payload["period"] == "monthly"
    assert result.payload["starts_on"] == "2026-03-01"
    assert result.payload["category_names"] == ["Dining Out"]


def test_capture_goal_message_returns_ambiguous_subject_clarify_for_category_vs_merchant_collision() -> None:
    class _AmbiguousFinanceRead(_StubFinanceRead):
        def list_goal_category_names(self) -> list[str]:
            return ["Cafe", "Groceries"]

    result = capture_goal_message(
        message="Make a goal to spend less than $60 at Cafe this week",
        review_date="2026-03-15",
        finance_api=_AmbiguousFinanceRead(),
        goals=[],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "ambiguous_subject"
    assert result.action == "goal_create"
    assert len(result.options) == 2


def test_capture_goal_message_builds_update_payload_for_pause() -> None:
    goals = [
        GoalRecord(
            id=7,
            goal_type="spending_cap",
            title="Dining Out Spending Cap",
            status="active",
            metric_type="sum_below",
            target_value=25_000,
            period="monthly",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-03-01",
            ends_on=None,
            notes=None,
            created_at="2026-03-01 00:00:00",
            updated_at="2026-03-01 00:00:00",
        )
    ]

    result = capture_goal_message(
        message="Pause my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=goals,
    )

    assert result.result_type == "update"
    assert result.goal_id == 7
    assert result.payload == {"status": "paused"}


def test_capture_goal_message_returns_missing_goal_for_supported_update_without_target() -> None:
    result = capture_goal_message(
        message="Pause my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "missing_goal"


def test_capture_goal_message_returns_no_match_for_unsupported_goal_family() -> None:
    goals = [
        GoalRecord(
            id=3,
            goal_type="habit",
            title="Walk 10k steps",
            status="active",
            metric_type="count_above",
            target_value=10,
            period="daily",
            domain="finance",
            category_names=[],
            merchant_names=[],
            account_names=[],
            starts_on="2026-03-01",
            ends_on=None,
            notes=None,
            created_at="2026-03-01 00:00:00",
            updated_at="2026-03-01 00:00:00",
        )
    ]

    result = capture_goal_message(
        message="Change my walk goal to $400",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=goals,
    )

    assert result.result_type == "no_match"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_goal_capture.py -q`
Expected: FAIL with `ModuleNotFoundError` for `minx_mcp.core.goal_capture`.

- [ ] **Step 3: Add typed capture result models and minimal interpreter implementation**

```python
@dataclass(frozen=True)
class GoalCaptureOption:
    goal_id: int | None = None
    title: str | None = None
    period: str | None = None
    target_value: int | None = None
    status: str | None = None
    filter_summary: str | None = None
    kind: str | None = None
    label: str | None = None
    payload_fragment: dict[str, object] | None = None


@dataclass(frozen=True)
class GoalCaptureResult:
    result_type: str
    assistant_message: str | None = None
    action: str | None = None
    payload: dict[str, object] | None = None
    goal_id: int | None = None
    clarification_type: str | None = None
    question: str | None = None
    options: list[GoalCaptureOption] | None = None
    resume_payload: dict[str, object] | None = None
```

```python
def capture_goal_message(
    *,
    message: str,
    review_date: str,
    finance_api: FinanceReadInterface,
    goals: list[GoalRecord],
) -> GoalCaptureResult:
    normalized = _normalize_message(message)
    create_result = _try_capture_create(normalized, review_date, finance_api)
    if create_result is not None:
        return create_result
    update_result = _try_capture_update(normalized, goals)
    if update_result is not None:
        return update_result
    return GoalCaptureResult(
        result_type="no_match",
        assistant_message="I couldn't map that to a supported finance goal action.",
    )
```

```python
def _supported_conversational_goal(goal: GoalRecord) -> bool:
    return (
        goal.goal_type == "spending_cap"
        and goal.metric_type == "sum_below"
        and goal.domain == "finance"
        and goal.status in {"active", "paused"}
    )


def _default_starts_on(review_date: str) -> str:
    return review_date
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_goal_capture.py -q`
Expected: PASS with all create/update/clarify/no-match tests green.

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/core/models.py minx_mcp/core/goal_capture.py tests/test_goal_capture.py
git commit -m "feat: add deterministic goal capture interpreter"
```

---

### Task 3: Wire `goal_capture` Into The Core MCP Server

**Files:**
- Modify: `minx_mcp/core/server.py`
- Modify: `tests/test_core_server.py`

- [ ] **Step 1: Write failing server-boundary tests for `goal_capture`**

```python
def test_core_server_registers_goal_capture_tool_name(tmp_path: Path) -> None:
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    assert server._tool_manager.get_tool("goal_capture").name == "goal_capture"


def test_goal_capture_returns_invalid_input_for_blank_message(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(message="   ", review_date="2026-03-15")

    assert result == {
        "success": False,
        "data": None,
        "error": "message must be non-empty after trimming",
        "error_code": "INVALID_INPUT",
    }


def test_goal_capture_returns_create_payload_with_explicit_starts_on(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = goal_capture(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"
    assert result["data"]["payload"]["starts_on"] == "2026-03-01"
```

- [ ] **Step 2: Run the server tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core_server.py -k "goal_capture" -q`
Expected: FAIL because `goal_capture` is not registered yet.

- [ ] **Step 3: Register the MCP tool and add the server helper**

```python
@mcp.tool(name="goal_capture")
def goal_capture(
    message: str,
    review_date: str | None = None,
) -> dict[str, object]:
    return wrap_tool_call(lambda: _goal_capture(config, message, review_date))
```

```python
def _goal_capture(
    config: CoreServiceConfig,
    message: str,
    review_date: str | None,
) -> dict[str, object]:
    normalized_message = message.strip()
    if not normalized_message:
        raise InvalidInputError("message must be non-empty after trimming")
    if len(normalized_message) > 500:
        raise InvalidInputError("message must be at most 500 characters")
    effective_review_date = _resolve_review_date(review_date)
    conn = get_connection(config.db_path)
    try:
        goal_service = GoalService(conn)
        goals = goal_service.list_goals(status=None) + goal_service.list_goals(status="paused")
        result = capture_goal_message(
            message=normalized_message,
            review_date=effective_review_date,
            finance_api=FinanceReadAPI(conn),
            goals=goals,
        )
        return asdict(result)
    finally:
        conn.close()
```

- [ ] **Step 4: Run the targeted server tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_core_server.py -k "goal_capture" -q`
Expected: PASS with registration, validation, and payload-shape coverage green.

- [ ] **Step 5: Commit**

```bash
git add minx_mcp/core/server.py tests/test_core_server.py
git commit -m "feat: expose goal capture through the core server"
```

---

### Task 4: Prove The Conversational Flow Through Stdio And Repo E2E Tests

**Files:**
- Modify: `tests/test_core_mcp_stdio.py`
- Modify: `tests/test_core_server.py`

- [ ] **Step 1: Write failing end-to-end tests for create, `goal_get`, update, and protected review**

```python
@pytest.mark.asyncio
async def test_core_server_stdio_goal_capture_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fingerprint')
        """
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, "2026-03-15", "Lunch", "Cafe", -1200, 3, "manual"),
    )
    conn.commit()
    conn.close()

    # initialize stdio session...
    # call goal_capture(create)
    # call goal_create with returned payload
    # call goal_get and assert actual_value == 1200
    # call goal_capture(update)
    # call goal_update with returned payload
    # call goal_get and assert status == "paused"
    # call daily_review and assert the protected projection has no raw goal_progress field
```

```python
def test_goal_capture_repo_e2e_flow_exercises_progress_before_protected_review(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    config = _TestConfig(db_path, tmp_path / "vault")

    create_result = _goal_capture(
        config,
        "Make a goal to spend less than $250 on dining out this month",
        "2026-03-15",
    )
    created_goal = _goal_create(config, GoalCreateInput(**create_result["payload"]))

    conn = get_connection(db_path)
    _seed_matching_dining_transaction(conn, posted_at="2026-03-15", amount_cents=-1200)
    conn.commit()
    conn.close()

    progress_before_update = _goal_get(config, created_goal["goal"]["id"], "2026-03-15")
    assert progress_before_update["progress"]["actual_value"] == 1200

    update_result = _goal_capture(config, "Pause my dining out goal", "2026-03-15")
    _goal_update(config, update_result["goal_id"], GoalUpdateInput(**update_result["payload"]))

    progress_after_update = _goal_get(config, created_goal["goal"]["id"], "2026-03-15")
    assert progress_after_update["goal"]["status"] == "paused"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core_server.py -k "repo_e2e_flow_exercises_progress" tests/test_core_mcp_stdio.py::test_core_server_stdio_goal_capture_flow -q`
Expected: FAIL because the stdio/server flow does not expose `goal_capture` yet.

- [ ] **Step 3: Fill in the minimal test helpers and stdio expectations**

```python
tools_result = await session.list_tools()
tool_names = [tool.name for tool in tools_result.tools]
assert tool_names == [
    "daily_review",
    "goal_capture",
    "goal_create",
    "goal_list",
    "goal_get",
    "goal_update",
    "goal_archive",
]

captured = await session.call_tool(
    "goal_capture",
    {
        "message": "Make a goal to spend less than $250 on dining out this month",
        "review_date": "2026-03-15",
    },
)
assert captured.structuredContent["data"]["result_type"] == "create"
assert captured.structuredContent["data"]["payload"]["starts_on"] == "2026-03-01"
```

```python
review = await session.call_tool(
    "daily_review",
    {"review_date": "2026-03-15", "force": False},
)
assert review.structuredContent["data"]["redaction_applied"] is True
assert "goal_progress" not in review.structuredContent["data"]
```

- [ ] **Step 4: Run the full targeted verification for the feature slice**

Run: `.venv/bin/python -m pytest tests/test_finance_read_api.py -k "list_goal_category_names or list_spending_merchant_names" tests/test_goal_capture.py tests/test_core_server.py -k "goal_capture or repo_e2e_flow_exercises_progress" tests/test_core_mcp_stdio.py::test_core_server_stdio_goal_capture_flow -q`
Expected: PASS with all new goal-capture coverage green.

- [ ] **Step 5: Commit**

```bash
git add tests/test_core_server.py tests/test_core_mcp_stdio.py
git commit -m "test: cover goal capture end to end"
```

---

## Self-Review

- Spec coverage: the plan covers the read-boundary extension, deterministic create/update/clarify/no-match behavior, explicit `starts_on` capture-time defaults, supported-family update scoping, MCP tool wiring, stdio coverage, and the `goal_get`-based E2E verification before protected review.
- Placeholder scan: no `TODO`/`TBD` placeholders remain; each task lists concrete files, commands, and representative code.
- Type consistency: the plan uses `GoalCaptureResult`, `GoalCaptureOption`, `capture_goal_message()`, `list_goal_category_names()`, and `list_spending_merchant_names()` consistently across models, implementation, and tests.
