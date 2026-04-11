**Status: Completed (historical).** This plan was executed in an earlier slice. The codebase has since evolved — see [HANDOFF.md](/Users/akmini/Documents/minx-mcp/HANDOFF.md) for current state.

# Slice 2 Goals And Drift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add goal management, goal-aware review behavior, drift detection, and optional OpenAI-compatible LLM enrichment to `minx-core` without changing the repo's local-first single-user model.

**Architecture:** Keep `Minx Core` as the owner of goals, progress calculation, and review interpretation. Extend the finance read boundary only where goals require filtered query support, make the `daily_review` MCP tool async-safe at the contract layer, wire one real OpenAI-compatible provider into the existing LLM abstraction, and add goal-aware read models plus detectors without introducing a new persistence-heavy analytics subsystem.

**Tech Stack:** Python 3.12+, SQLite, dataclasses, FastMCP, `httpx`, pytest, pytest-asyncio, mypy, existing `FinanceReadAPI`, existing `VaultWriter`, existing `preferences` storage

---

## Scope Note

This plan covers the Slice 2 work that belongs in this repository.

It intentionally stops at the repo boundary:

- `minx-core` goal tools are included
- goal-aware review behavior is included
- Hermes/Discord conversational goal capture is **not** implemented here because no Hermes/Discord code lives in this repo

The future Hermes/Discord connection is unblocked by exposing stable structured goal tools and stable goal payload shapes from `minx-core`.

## File Structure

**Create**

- `minx_mcp/core/llm_openai.py`
  OpenAI-compatible provider config parsing and HTTP-backed `LLMInterface` implementation.
- `minx_mcp/core/goals.py`
  Goal CRUD repository/service layer and goal validation helpers.
- `minx_mcp/core/goal_progress.py`
  Derived goal progress calculations built from active goals plus finance reads.
- `minx_mcp/core/goal_detectors.py`
  Goal-aware detector functions separated from the existing Slice 1 detector file.
- `minx_mcp/schema/migrations/007_core_goals.sql`
  Runtime migration adding the `goals` table.
- `schema/migrations/007_core_goals.sql`
  Repository mirror of the runtime migration.
- `tests/test_goals.py`
  Focused CRUD and validation coverage for the goal repository/service layer.
- `tests/test_goal_progress.py`
  Goal progress calculation coverage independent of the full review pipeline.
- `tests/conftest.py`
  Shared fixtures/helpers extracted from review and core-server tests once Slice 2 adds more goal-related cases.

**Modify**

- `minx_mcp/contracts.py`
  Add an async MCP wrapper alongside the existing sync wrapper.
- `minx_mcp/core/models.py`
  Add goal and goal-progress dataclasses, extend `ReadModels`, `DailyReview`, and `LLMInterface`.
- `minx_mcp/core/server.py`
  Make `daily_review` async-safe and add the goal CRUD tools.
- `minx_mcp/core/llm.py`
  Register the OpenAI-compatible provider and extend prompt inputs with goal progress context.
- `minx_mcp/core/review.py`
  Build goal-aware read models, feed goal progress into the LLM path, and render goal status in the review artifact.
- `minx_mcp/core/read_models.py`
  Continue building Slice 1 read models, but also source active goals and pass them to `GoalProgress`.
- `minx_mcp/core/detectors.py`
  Import and register the new goal detectors in a stable order.
- `minx_mcp/finance/read_api.py`
  Add filtered total/count methods needed by goal progress and category drift.
- `tests/test_core_server.py`
  Cover the async `daily_review` tool path and the new goal tools.
- `tests/test_review.py`
  Cover goal-aware fallback reviews, goal-aware enriched reviews, and persistence behavior.
- `tests/test_read_models.py`
  Cover goal-aware read model construction and timezone-safe date handling.
- `tests/test_detectors.py`
  Cover `detect_goal_drift`, `detect_category_drift`, and detector registry order.
- `tests/test_llm.py`
  Cover provider construction and OpenAI-compatible normalization behavior.
- `tests/test_finance_read_api.py`
  Cover finance filtered totals/counts for goal filters.
- `tests/test_db.py`
  Cover the `goals` migration and migration ordering.
- `README.md`
  Document the new Core goal tools and optional OpenAI-compatible config.
- `docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md`
  Mark Slice 2 implemented with notes once verification is green.

**Leave Untouched Unless Required**

- `HANDOFF.md`
- `docs/superpowers/specs/2026-04-07-slice2-goals-drift-design.md`
- finance import/report code that is not directly touched by goal query support

Those files should not be edited during implementation unless the work truly forces it.

## Task 1: Make `daily_review` Async-Safe At The MCP Boundary

**Files:**
- Modify: `minx_mcp/contracts.py`
- Modify: `minx_mcp/core/server.py`
- Modify: `tests/test_core_server.py`

- [ ] **Step 1: Add a failing async tool test that awaits the registered `daily_review` MCP function**

```python
@pytest.mark.asyncio
async def test_daily_review_tool_function_is_awaitable_and_returns_contract_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    daily_review = server._tool_manager.get_tool("daily_review").fn

    result = await daily_review("2026-03-15", False)

    assert result["success"] is True
    assert result["data"]["date"] == "2026-03-15"
    assert isinstance(result["data"]["markdown"], str)
```

- [ ] **Step 2: Add a failing async validation test for the contract error path**

```python
@pytest.mark.asyncio
async def test_daily_review_tool_returns_invalid_input_contract_for_bad_date(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    daily_review = server._tool_manager.get_tool("daily_review").fn

    result = await daily_review("not-a-date", False)

    assert result == {
        "success": False,
        "data": None,
        "error": "review_date must be a valid ISO date",
        "error_code": "INVALID_INPUT",
    }
```

- [ ] **Step 3: Run the core-server tests to verify they fail on the current sync wrapper**

Run: `.venv/bin/python -m pytest tests/test_core_server.py -v`
Expected: FAIL because the registered tool function is not awaitable and the sync contract wrapper cannot `await` async work.

- [ ] **Step 4: Add an async MCP wrapper in `minx_mcp/contracts.py`**

```python
from collections.abc import Awaitable, Callable


async def wrap_async_tool_call(fn: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    try:
        return ok(await fn())
    except MinxContractError as exc:
        return fail(exc.message, exc.error_code)
    except Exception:
        logger.exception("Unexpected exception in MCP tool")
        return fail("Internal server error", INTERNAL_ERROR)
```

- [ ] **Step 5: Convert the Core tool handler and helper to async**

```python
@mcp.tool(name="daily_review")
async def daily_review(
    review_date: str | None = None,
    force: bool = False,
) -> dict[str, object]:
    return await wrap_async_tool_call(
        lambda: _daily_review(config, review_date, force),
    )


async def _daily_review(
    config: CoreServiceConfig,
    review_date: str | None,
    force: bool,
) -> dict[str, object]:
    effective_date = review_date or date.today().isoformat()
    try:
        date.fromisoformat(effective_date)
    except ValueError as exc:
        raise InvalidInputError("review_date must be a valid ISO date") from exc

    ctx = ReviewContext(
        db_path=config.db_path,
        finance_api=None,
        vault_writer=VaultWriter(config.vault_path, ("Minx",)),
        llm=None,
    )
    artifact = await generate_daily_review(effective_date, ctx, force=force)
    return {
        "date": artifact.date,
        "narrative": artifact.narrative,
        "next_day_focus": artifact.next_day_focus,
        "insight_count": len(artifact.insights),
        "llm_enriched": artifact.llm_enriched,
        "timeline_entry_count": len(artifact.timeline.entries),
        "open_loop_count": len(artifact.open_loops.loops),
        "markdown": render_daily_review_markdown(artifact),
    }
```

Delete the old `asyncio.new_event_loop()` / `run_until_complete()` path completely.

- [ ] **Step 6: Update the existing helper-based tests to await `_daily_review()` directly**

```python
@pytest.mark.asyncio
async def test_daily_review_tool_defaults_to_today(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    config = _TestConfig(db_path, tmp_path / "vault")

    result = await _daily_review(config, None, False)

    assert isinstance(result["date"], str)
```

- [ ] **Step 7: Run the core-server tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_core_server.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add minx_mcp/contracts.py minx_mcp/core/server.py tests/test_core_server.py
git commit -m "feat: make core daily review async-safe"
```

## Task 2: Wire An OpenAI-Compatible Review Provider

**Files:**
- Create: `minx_mcp/core/llm_openai.py`
- Modify: `minx_mcp/core/llm.py`
- Modify: `tests/test_llm.py`
- Modify: `tests/test_review.py`

- [ ] **Step 1: Add a failing provider-construction test**

```python
def test_create_llm_builds_openai_compatible_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from minx_mcp.core.llm import create_llm
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    created = create_llm(
        {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
        }
    )

    assert isinstance(created, OpenAICompatibleLLM)
```

- [ ] **Step 2: Add a failing async provider-response test**

```python
@pytest.mark.asyncio
async def test_openai_compatible_llm_posts_chat_completion_and_normalizes_json(
    monkeypatch,
):
    from minx_mcp.core.llm_openai import OpenAICompatibleLLM

    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"additional_insights": [], '
                                '"narrative": "Goal-aware review.", '
                                '"next_day_focus": ["Stay under budget"]}'
                            )
                        }
                    }
                ]
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url: str, headers: dict[str, str], json: dict[str, object]):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("minx_mcp.core.llm_openai.httpx.AsyncClient", _FakeClient)

    llm = OpenAICompatibleLLM(
        base_url="https://api.example.com/v1",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        timeout_seconds=15.0,
    )
    result = await llm.evaluate_review(
        timeline=_timeline(),
        spending=_spending(),
        open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
        detector_insights=[],
    )

    assert result.narrative == "Goal-aware review."
    assert result.next_day_focus == ["Stay under budget"]
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
```

- [ ] **Step 3: Run the LLM tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_llm.py -v`
Expected: FAIL because `openai_compatible` is not registered yet.

- [ ] **Step 4: Create `minx_mcp/core/llm_openai.py`**

```python
from dataclasses import dataclass
import os

import httpx

from minx_mcp.core.llm import LLMProviderError, normalize_review_result
from minx_mcp.core.models import LLMInterface


@dataclass(frozen=True)
class OpenAICompatibleLLM(LLMInterface):
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float = 30.0

    async def evaluate_review(
        self,
        timeline,
        spending,
        open_loops,
        detector_insights,
    ):
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise LLMProviderError(
                f"Missing API key environment variable: {self.api_key_env}"
            )

        prompt = _render_openai_prompt(
            timeline=timeline,
            spending=spending,
            open_loops=open_loops,
            detector_insights=detector_insights,
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        return normalize_review_result(content)
```

- [ ] **Step 5: Register the provider in `minx_mcp/core/llm.py`**

```python
from minx_mcp.core.llm_openai import OpenAICompatibleLLM


def _build_openai_compatible(config: dict[str, Any]) -> LLMInterface:
    return OpenAICompatibleLLM(
        base_url=str(config["base_url"]),
        model=str(config["model"]),
        api_key_env=str(config["api_key_env"]),
        timeout_seconds=float(config.get("timeout_seconds", 30.0)),
    )


_PROVIDER_BUILDERS: dict[str, Callable[[dict[str, Any]], LLMInterface | None]] = {
    "openai_compatible": _build_openai_compatible,
}
```

- [ ] **Step 6: Add an integration test in `tests/test_review.py` that loads `openai_compatible` from `core/llm_config`**

```python
@pytest.mark.asyncio
async def test_generate_daily_review_loads_openai_compatible_provider_from_preference(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    set_preference(
        conn,
        "core",
        "llm_config",
        {
            "provider": "openai_compatible",
            "base_url": "https://api.example.com/v1",
            "model": "gpt-4o-mini",
            "api_key_env": "OPENAI_API_KEY",
        },
    )
    conn.commit()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        "minx_mcp.core.llm_openai.httpx.AsyncClient",
        _FakeOpenAIAsyncClient,
    )

    artifact = await generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_attention_items(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
    )

    assert artifact.llm_enriched is True
    assert artifact.narrative == "Goal-aware review."
```

- [ ] **Step 7: Run the LLM and review tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_llm.py tests/test_review.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add minx_mcp/core/llm.py minx_mcp/core/llm_openai.py tests/test_llm.py tests/test_review.py
git commit -m "feat: add openai-compatible review provider"
```

## Task 3: Extend The Finance Read Boundary For Goal Filters

**Files:**
- Modify: `minx_mcp/core/models.py`
- Modify: `minx_mcp/finance/read_api.py`
- Modify: `tests/test_finance_read_api.py`
- Modify: `tests/test_review.py`

- [ ] **Step 1: Add a failing finance read API test for filtered spend totals**

```python
def test_get_filtered_spending_total_respects_category_and_merchant_filters(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Lunch",
        merchant="Cafe",
        amount_cents=-1200,
        category_id=3,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Groceries",
        merchant="HEB",
        amount_cents=-4500,
        category_id=2,
    )
    conn.commit()

    api = FinanceReadAPI(conn)

    total = api.get_filtered_spending_total(
        "2026-03-15",
        "2026-03-15",
        category_names=["Dining Out"],
        merchant_names=["Cafe"],
    )

    assert total == 1200
```

- [ ] **Step 2: Add a failing finance read API test for filtered transaction counts**

```python
def test_get_filtered_transaction_count_counts_matching_expense_rows(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Dinner",
        merchant="Cafe",
        amount_cents=-1200,
        category_id=3,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-15",
        description="Coffee",
        merchant="Cafe",
        amount_cents=-500,
        category_id=3,
    )
    conn.commit()

    api = FinanceReadAPI(conn)

    count = api.get_filtered_transaction_count(
        "2026-03-15",
        "2026-03-15",
        category_names=["Dining Out"],
        merchant_names=["Cafe"],
    )

    assert count == 2
```

- [ ] **Step 3: Run the finance read API tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_finance_read_api.py -v`
Expected: FAIL because the filtered methods do not exist yet.

- [ ] **Step 4: Extend `FinanceReadInterface` in `minx_mcp/core/models.py`**

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
    def get_filtered_spending_total(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int: ...
    def get_filtered_transaction_count(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int: ...
```

- [ ] **Step 5: Implement the new filtered query methods in `minx_mcp/finance/read_api.py`**

```python
def get_filtered_spending_total(
    self,
    start_date: str,
    end_date: str,
    *,
    category_names: list[str] | None = None,
    merchant_names: list[str] | None = None,
    account_names: list[str] | None = None,
) -> int:
    sql, params = _build_filtered_expense_query(
        aggregate_sql="COALESCE(ABS(SUM(t.amount_cents)), 0) AS value",
        start_date=start_date,
        end_date=end_date,
        category_names=category_names,
        merchant_names=merchant_names,
        account_names=account_names,
    )
    row = self._db.execute(sql, params).fetchone()
    return int(row["value"])


def get_filtered_transaction_count(
    self,
    start_date: str,
    end_date: str,
    *,
    category_names: list[str] | None = None,
    merchant_names: list[str] | None = None,
    account_names: list[str] | None = None,
) -> int:
    sql, params = _build_filtered_expense_query(
        aggregate_sql="COUNT(*) AS value",
        start_date=start_date,
        end_date=end_date,
        category_names=category_names,
        merchant_names=merchant_names,
        account_names=account_names,
    )
    row = self._db.execute(sql, params).fetchone()
    return int(row["value"])
```

Implement `_build_filtered_expense_query()` once and share it between both methods.

- [ ] **Step 6: Update the finance API doubles in `tests/test_review.py` to implement the new protocol methods**

```python
def get_filtered_spending_total(
    self,
    start_date: str,
    end_date: str,
    *,
    category_names=None,
    merchant_names=None,
    account_names=None,
) -> int:
    return self._filtered_total_spent_cents


def get_filtered_transaction_count(
    self,
    start_date: str,
    end_date: str,
    *,
    category_names=None,
    merchant_names=None,
    account_names=None,
) -> int:
    return self._filtered_transaction_count
```

- [ ] **Step 7: Run the finance read API tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_finance_read_api.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add minx_mcp/core/models.py minx_mcp/finance/read_api.py tests/test_finance_read_api.py tests/test_review.py
git commit -m "feat: add filtered finance reads for goals"
```

## Task 4: Add The Goals Migration

**Files:**
- Create: `minx_mcp/schema/migrations/007_core_goals.sql`
- Create: `schema/migrations/007_core_goals.sql`
- Modify: `tests/test_db.py`

- [ ] **Step 1: Add a failing DB bootstrap test for the `goals` table**

```python
def test_database_bootstrap_creates_goals_table(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    columns = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(goals)").fetchall()
    }

    assert list(columns) == [
        "id",
        "goal_type",
        "title",
        "status",
        "metric_type",
        "target_value",
        "period",
        "domain",
        "filters_json",
        "starts_on",
        "ends_on",
        "notes",
        "created_at",
        "updated_at",
    ]
```

- [ ] **Step 2: Add a failing migration-order assertion update**

```python
def test_migrations_are_idempotent(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    applied = conn.execute("SELECT name FROM _migrations ORDER BY name").fetchall()

    assert [row["name"] for row in applied] == [
        "001_platform.sql",
        "002_finance.sql",
        "003_finance_views.sql",
        "004_finance_amount_cents.sql",
        "005_core.sql",
        "006_finance_report_lifecycle.sql",
        "007_core_goals.sql",
    ]
```

- [ ] **Step 3: Run the DB tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: FAIL because the new migration does not exist yet.

- [ ] **Step 4: Add the runtime and mirrored migration files**

```sql
CREATE TABLE IF NOT EXISTS goals (
    id            INTEGER PRIMARY KEY,
    goal_type     TEXT NOT NULL,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    metric_type   TEXT NOT NULL,
    target_value  INTEGER NOT NULL,
    period        TEXT NOT NULL,
    domain        TEXT NOT NULL,
    filters_json  TEXT NOT NULL,
    starts_on     TEXT NOT NULL,
    ends_on       TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_goals_status_domain
ON goals(status, domain);

CREATE INDEX IF NOT EXISTS idx_goals_period_status
ON goals(period, status);
```

Keep the two `007_core_goals.sql` files byte-for-byte identical.

- [ ] **Step 5: Run the DB tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/schema/migrations/007_core_goals.sql schema/migrations/007_core_goals.sql tests/test_db.py
git commit -m "feat: add core goals migration"
```

## Task 5: Build The Goal Repository And Validation Layer

**Files:**
- Modify: `minx_mcp/core/models.py`
- Create: `minx_mcp/core/goals.py`
- Create: `tests/test_goals.py`

- [ ] **Step 1: Add a failing goal CRUD test**

```python
def test_goal_service_create_get_update_archive_round_trip(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    from minx_mcp.core.goals import GoalService
    from minx_mcp.core.models import GoalCreateInput, GoalUpdateInput

    service = GoalService(conn)
    created = service.create_goal(
        GoalCreateInput(
            goal_type="spending_cap",
            title="Dining out under $250",
            metric_type="sum_below",
            target_value=25_000,
            period="monthly",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-03-01",
            ends_on=None,
            notes="March goal",
        )
    )
    fetched = service.get_goal(created.id)
    updated = service.update_goal(
        created.id,
        GoalUpdateInput(title="Dining out under $200", target_value=20_000),
    )
    archived = service.archive_goal(created.id)

    assert fetched.id == created.id
    assert updated.title == "Dining out under $200"
    assert updated.target_value == 20_000
    assert archived.status == "archived"
```

- [ ] **Step 2: Add a failing validation test for malformed finance filters**

```python
def test_goal_service_rejects_goal_without_any_finance_filter(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    from minx_mcp.contracts import InvalidInputError
    from minx_mcp.core.goals import GoalService
    from minx_mcp.core.models import GoalCreateInput

    service = GoalService(conn)

    with pytest.raises(InvalidInputError, match="at least one finance filter"):
        service.create_goal(
            GoalCreateInput(
                goal_type="spending_cap",
                title="Invalid broad goal",
                metric_type="sum_below",
                target_value=25_000,
                period="monthly",
                domain="finance",
                category_names=[],
                merchant_names=[],
                account_names=[],
                starts_on="2026-03-01",
                ends_on=None,
                notes=None,
            )
        )
```

- [ ] **Step 3: Run the goal tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_goals.py -v`
Expected: FAIL because the goal models and service do not exist yet.

- [ ] **Step 4: Add the goal dataclasses to `minx_mcp/core/models.py`**

```python
@dataclass(frozen=True)
class GoalCreateInput:
    goal_type: str
    title: str
    metric_type: str
    target_value: int
    period: str
    domain: str
    category_names: list[str]
    merchant_names: list[str]
    account_names: list[str]
    starts_on: str
    ends_on: str | None
    notes: str | None


@dataclass(frozen=True)
class GoalUpdateInput:
    title: str | None = None
    target_value: int | None = None
    status: str | None = None
    ends_on: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class GoalRecord:
    id: int
    goal_type: str
    title: str
    status: str
    metric_type: str
    target_value: int
    period: str
    domain: str
    category_names: list[str]
    merchant_names: list[str]
    account_names: list[str]
    starts_on: str
    ends_on: str | None
    notes: str | None
    created_at: str
    updated_at: str
```

- [ ] **Step 5: Implement the repository/service in `minx_mcp/core/goals.py`**

```python
class GoalService:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def create_goal(self, payload: GoalCreateInput) -> GoalRecord:
        _validate_goal_create(payload)
        filters_json = json.dumps(
            {
                "category_names": payload.category_names,
                "merchant_names": payload.merchant_names,
                "account_names": payload.account_names,
            }
        )
        cursor = self._conn.execute(
            """
            INSERT INTO goals (
                goal_type, title, status, metric_type, target_value, period, domain,
                filters_json, starts_on, ends_on, notes, created_at, updated_at
            ) VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                payload.goal_type,
                payload.title,
                payload.metric_type,
                payload.target_value,
                payload.period,
                payload.domain,
                filters_json,
                payload.starts_on,
                payload.ends_on,
                payload.notes,
            ),
        )
        self._conn.commit()
        return self.get_goal(int(cursor.lastrowid))
```

Also implement:

- `get_goal(goal_id: int) -> GoalRecord`
- `list_goals(status: str | None = None) -> list[GoalRecord]`
- `update_goal(goal_id: int, payload: GoalUpdateInput) -> GoalRecord`
- `archive_goal(goal_id: int) -> GoalRecord`
- `list_active_goals(review_date: str) -> list[GoalRecord]`

Validation must explicitly allow only:

- `metric_type in {"sum_below", "sum_above", "count_below", "count_above"}`
- `period in {"daily", "weekly", "monthly", "rolling_28d"}`
- `domain == "finance"` for the first Slice 2 implementation

- [ ] **Step 6: Run the goal tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_goals.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add minx_mcp/core/models.py minx_mcp/core/goals.py tests/test_goals.py
git commit -m "feat: add core goal repository"
```

## Task 6: Expose Goal CRUD Through `minx-core`

**Files:**
- Modify: `minx_mcp/core/server.py`
- Modify: `tests/test_core_server.py`

- [ ] **Step 1: Add a failing tool-registration test for the new Core tools**

```python
def test_core_server_registers_goal_tool_names(tmp_path: Path) -> None:
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    assert server._tool_manager.get_tool("daily_review").name == "daily_review"
    assert server._tool_manager.get_tool("goal_create").name == "goal_create"
    assert server._tool_manager.get_tool("goal_list").name == "goal_list"
    assert server._tool_manager.get_tool("goal_get").name == "goal_get"
    assert server._tool_manager.get_tool("goal_update").name == "goal_update"
    assert server._tool_manager.get_tool("goal_archive").name == "goal_archive"
```

- [ ] **Step 2: Add a failing goal tool round-trip test**

```python
def test_goal_create_and_list_tools_round_trip(tmp_path: Path) -> None:
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_list = server._tool_manager.get_tool("goal_list").fn

    created = goal_create(
        title="Dining out under $250",
        goal_type="spending_cap",
        metric_type="sum_below",
        target_value=25_000,
        period="monthly",
        domain="finance",
        category_names=["Dining Out"],
        merchant_names=[],
        account_names=[],
        starts_on="2026-03-01",
        ends_on=None,
        notes="March goal",
    )
    listed = goal_list(status="active")

    assert created["success"] is True
    assert created["data"]["goal"]["title"] == "Dining out under $250"
    assert listed["success"] is True
    assert [goal["title"] for goal in listed["data"]["goals"]] == ["Dining out under $250"]
```

- [ ] **Step 3: Run the core-server tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core_server.py -v`
Expected: FAIL because the goal tools are not registered yet.

- [ ] **Step 4: Add the new tools to `minx_mcp/core/server.py`**

```python
@mcp.tool(name="goal_create")
def goal_create(
    title: str,
    goal_type: str,
    metric_type: str,
    target_value: int,
    period: str,
    domain: str = "finance",
    category_names: list[str] | None = None,
    merchant_names: list[str] | None = None,
    account_names: list[str] | None = None,
    starts_on: str | None = None,
    ends_on: str | None = None,
    notes: str | None = None,
) -> dict[str, object]:
    return wrap_tool_call(
        lambda: _goal_create(
            config,
            title=title,
            goal_type=goal_type,
            metric_type=metric_type,
            target_value=target_value,
            period=period,
            domain=domain,
            category_names=category_names or [],
            merchant_names=merchant_names or [],
            account_names=account_names or [],
            starts_on=starts_on,
            ends_on=ends_on,
            notes=notes,
        )
    )
```

Implement matching `_goal_create()`, `_goal_list()`, `_goal_get()`, `_goal_update()`, and `_goal_archive()` helpers that open a DB connection, use `GoalService`, and return stable DTOs:

```python
{
    "goal": {
        "id": goal.id,
        "title": goal.title,
        "status": goal.status,
        "metric_type": goal.metric_type,
        "target_value": goal.target_value,
        "period": goal.period,
        "domain": goal.domain,
        "filters": {
            "category_names": goal.category_names,
            "merchant_names": goal.merchant_names,
            "account_names": goal.account_names,
        },
        "starts_on": goal.starts_on,
        "ends_on": goal.ends_on,
        "notes": goal.notes,
    }
}
```

- [ ] **Step 5: Run the core-server tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_core_server.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add minx_mcp/core/server.py tests/test_core_server.py
git commit -m "feat: add core goal tools"
```

## Task 7: Add Goal Progress And Goal-Aware Review Behavior

**Files:**
- Modify: `minx_mcp/core/models.py`
- Create: `minx_mcp/core/goal_progress.py`
- Modify: `minx_mcp/core/read_models.py`
- Modify: `minx_mcp/core/review.py`
- Modify: `minx_mcp/core/llm.py`
- Create: `tests/test_goal_progress.py`
- Modify: `tests/test_review.py`
- Modify: `tests/test_read_models.py`
- Modify: `tests/test_llm.py`

- [ ] **Step 1: Add a failing goal-progress unit test**

```python
def test_build_goal_progress_for_monthly_sum_below_goal(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    from minx_mcp.core.goal_progress import build_goal_progress
    from minx_mcp.core.models import GoalRecord

    goal = GoalRecord(
        id=1,
        goal_type="spending_cap",
        title="Dining out under $250",
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
        created_at="2026-03-01T00:00:00Z",
        updated_at="2026-03-01T00:00:00Z",
    )

    progress = build_goal_progress(
        conn,
        review_date="2026-03-15",
        goals=[goal],
        finance_api=_GoalFinanceAPIDouble(total=12_000, count=3),
    )

    assert len(progress) == 1
    assert progress[0].status == "on_track"
    assert progress[0].actual_value == 12_000
    assert progress[0].remaining_value == 13_000
```

- [ ] **Step 2: Add a failing review test for goal-aware fallback narrative and markdown**

```python
@pytest.mark.asyncio
async def test_generate_daily_review_includes_goal_status_in_fallback_narrative_and_note(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.execute(
        """
        INSERT INTO goals (
            goal_type, title, status, metric_type, target_value, period, domain,
            filters_json, starts_on, ends_on, notes, created_at, updated_at
        ) VALUES (
            'spending_cap', 'Dining out under $250', 'active', 'sum_below', 25000, 'monthly', 'finance',
            '{"category_names": ["Dining Out"], "merchant_names": [], "account_names": []}',
            '2026-03-01', NULL, NULL, datetime('now'), datetime('now')
        )
        """
    )
    conn.commit()

    import minx_mcp.core.review as review
    monkeypatch.setattr(review, "create_llm", lambda config=None, db_path=None: None)

    artifact = await review.generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_goal_totals(filtered_total=12_000, filtered_count=3),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
    )

    assert len(artifact.goal_progress) == 1
    assert "Dining out under $250" in artifact.narrative
    assert "## Goals" in render_daily_review_markdown(artifact)
```

- [ ] **Step 3: Run the goal-progress, review, read-model, and LLM tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_goal_progress.py tests/test_review.py tests/test_read_models.py tests/test_llm.py -v`
Expected: FAIL because goal progress does not exist, the review artifact has no goal section, and the LLM interface does not accept goal context yet.

- [ ] **Step 4: Extend the core dataclasses in `minx_mcp/core/models.py`**

```python
@dataclass(frozen=True)
class GoalProgress:
    goal_id: int
    title: str
    metric_type: str
    target_value: int
    actual_value: int
    remaining_value: int | None
    current_start: str
    current_end: str
    status: str
    summary: str
    category_names: list[str]
    merchant_names: list[str]
    account_names: list[str]


@dataclass(frozen=True)
class ReadModels:
    timeline: DailyTimeline
    spending: SpendingSnapshot
    open_loops: OpenLoopsSnapshot
    goal_progress: list[GoalProgress]


@dataclass(frozen=True)
class DailyReview:
    date: str
    timeline: DailyTimeline
    spending: SpendingSnapshot
    open_loops: OpenLoopsSnapshot
    goal_progress: list[GoalProgress]
    insights: list[InsightCandidate]
    narrative: str
    next_day_focus: list[str]
    llm_enriched: bool
```

Update `LLMInterface.evaluate_review(...)` and every in-repo implementation/double to accept `goal_progress`.

- [ ] **Step 5: Implement `build_goal_progress()` in `minx_mcp/core/goal_progress.py`**

```python
def build_goal_progress(
    conn: Connection,
    review_date: str,
    goals: list[GoalRecord],
    finance_api: FinanceReadInterface | None = None,
) -> list[GoalProgress]:
    finance_api = finance_api or FinanceReadAPI(conn)
    progress: list[GoalProgress] = []
    for goal in goals:
        current_start, current_end = _period_window(goal.period, review_date)
        total = finance_api.get_filtered_spending_total(
            current_start,
            current_end,
            category_names=goal.category_names,
            merchant_names=goal.merchant_names,
            account_names=goal.account_names,
        )
        count = finance_api.get_filtered_transaction_count(
            current_start,
            current_end,
            category_names=goal.category_names,
            merchant_names=goal.merchant_names,
            account_names=goal.account_names,
        )
        actual = total if goal.metric_type.startswith("sum_") else count
        progress.append(_progress_for_goal(goal, actual, current_start, current_end))
    return progress
```

- [ ] **Step 6: Update `minx_mcp/core/read_models.py` to source active goals and compute `goal_progress`**

```python
def build_read_models(
    conn: Connection,
    review_date: str,
    finance_api: FinanceReadInterface | None = None,
) -> ReadModels:
    finance_api = finance_api or FinanceReadAPI(conn)
    goals = GoalService(conn).list_active_goals(review_date)
    return ReadModels(
        timeline=build_daily_timeline(conn, review_date),
        spending=build_spending_snapshot(conn, review_date, finance_api=finance_api),
        open_loops=build_open_loops_snapshot(conn, review_date, finance_api=finance_api),
        goal_progress=build_goal_progress(
            conn,
            review_date,
            goals=goals,
            finance_api=finance_api,
        ),
    )
```

- [ ] **Step 7: Update the review pipeline and prompt layer**

```python
llm_result = await asyncio.wait_for(
    llm.evaluate_review(
        timeline=read_models.timeline,
        spending=read_models.spending,
        open_loops=read_models.open_loops,
        detector_insights=detector_insights,
        goal_progress=read_models.goal_progress,
    ),
    timeout=LLM_TIMEOUT_SECONDS,
)
```

Extend the fallback narrative with goal pressure:

```python
off_track = [goal for goal in read_models.goal_progress if goal.status == "off_track"]
if off_track:
    narrative_parts.append(
        f"{len(off_track)} active goal{'s are' if len(off_track) != 1 else ' is'} off track."
    )
```

Render a `## Goals` section in markdown:

```python
goal_lines = (
    [
        f"- [{goal.status}] {goal.title}: {goal.summary}"
        for goal in review.goal_progress
    ]
    or ["- No active goals."]
)
```

Add those lines between `## Spending` and `## Insights`.

- [ ] **Step 8: Update `_render_review_prompt()` in `minx_mcp/core/llm.py` to include compact goal context**

```python
goal_lines = [
    f"- {goal.title} | status={goal.status} | actual={goal.actual_value} | target={goal.target_value} | summary={goal.summary}"
    for goal in goal_progress
] or ["- No active goals."]
```

- [ ] **Step 9: Run the goal-progress, review, read-model, and LLM tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_goal_progress.py tests/test_review.py tests/test_read_models.py tests/test_llm.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add minx_mcp/core/models.py minx_mcp/core/goal_progress.py minx_mcp/core/read_models.py minx_mcp/core/review.py minx_mcp/core/llm.py tests/test_goal_progress.py tests/test_review.py tests/test_read_models.py tests/test_llm.py
git commit -m "feat: add goal-aware review progress"
```

## Task 8: Add Goal Drift And Category Drift Detectors

**Files:**
- Create: `minx_mcp/core/goal_detectors.py`
- Modify: `minx_mcp/core/detectors.py`
- Modify: `tests/test_detectors.py`

- [ ] **Step 1: Add a failing goal-drift detector test**

```python
def test_detect_goal_drift_returns_off_track_goal_insight():
    read_models = _build_read_models_with_goals(
        goal_progress=[
            GoalProgress(
                goal_id=1,
                title="Dining out under $250",
                metric_type="sum_below",
                target_value=25_000,
                actual_value=22_000,
                remaining_value=3_000,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="off_track",
                summary="Already at $220.00 with half the month remaining.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ]
    )

    from minx_mcp.core.goal_detectors import detect_goal_drift

    insights = detect_goal_drift(read_models)

    assert [_simplify(insight) for insight in insights] == [
        (
            "core.goal_drift",
            "2026-03-15:goal_drift:goal-1",
            "warning",
            "action_needed",
            ["Already at $220.00 with half the month remaining."],
        )
    ]
```

- [ ] **Step 2: Add a failing category-drift detector test**

```python
def test_detect_category_drift_returns_alert_for_large_goal_scoped_increase():
    read_models = _build_read_models_with_goals(
        goal_progress=[
            GoalProgress(
                goal_id=1,
                title="Dining out under $250",
                metric_type="sum_below",
                target_value=25_000,
                actual_value=18_000,
                remaining_value=7_000,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="watch",
                summary="Dining out is rising faster than expected.",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ]
    )

    from minx_mcp.core.goal_detectors import detect_category_drift

    insights = detect_category_drift(read_models)

    assert len(insights) == 1
    assert insights[0].insight_type == "finance.category_drift"
    assert insights[0].severity in {"warning", "alert"}
```

- [ ] **Step 3: Run the detector tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_detectors.py -v`
Expected: FAIL because goal-aware detectors do not exist yet.

- [ ] **Step 4: Create `minx_mcp/core/goal_detectors.py`**

```python
def detect_goal_drift(read_models: ReadModels) -> list[InsightCandidate]:
    insights: list[InsightCandidate] = []
    for goal in read_models.goal_progress:
        if goal.status != "off_track":
            continue
        insights.append(
            InsightCandidate(
                insight_type="core.goal_drift",
                dedupe_key=f"{read_models.timeline.date}:goal_drift:goal-{goal.goal_id}",
                summary=f"{goal.title} is off track.",
                supporting_signals=[goal.summary],
                confidence=0.9,
                severity="warning",
                actionability="action_needed",
                source="detector",
            )
        )
    return insights


def detect_category_drift(read_models: ReadModels) -> list[InsightCandidate]:
    insights: list[InsightCandidate] = []
    for goal in read_models.goal_progress:
        if goal.status not in {"watch", "off_track"}:
            continue
        if not goal.category_names:
            continue
        category_label = ", ".join(goal.category_names)
        insights.append(
            InsightCandidate(
                insight_type="finance.category_drift",
                dedupe_key=(
                    f"{read_models.timeline.date}:category_drift:"
                    f"goal-{goal.goal_id}:{category_label.lower().replace(' ', '-')}"
                ),
                summary=f"{category_label} spending is drifting away from {goal.title}.",
                supporting_signals=[goal.summary],
                confidence=0.82,
                severity="warning" if goal.status == "watch" else "alert",
                actionability="suggestion" if goal.status == "watch" else "action_needed",
                source="detector",
            )
        )
    return insights
```

- [ ] **Step 5: Register the new detectors in `minx_mcp/core/detectors.py` after the Slice 1 detectors**

```python
from minx_mcp.core.goal_detectors import detect_category_drift, detect_goal_drift

DETECTORS: list[DetectorFn] = [
    detect_spending_spike,
    detect_open_loops,
    detect_goal_drift,
    detect_category_drift,
]
```

- [ ] **Step 6: Run the detector tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_detectors.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add minx_mcp/core/goal_detectors.py minx_mcp/core/detectors.py tests/test_detectors.py
git commit -m "feat: add goal drift detectors"
```

## Task 9: Extract Shared Test Fixtures And Refresh Docs

**Files:**
- Create: `tests/conftest.py`
- Modify: `tests/test_core_server.py`
- Modify: `tests/test_review.py`
- Modify: `tests/test_goal_progress.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md`

- [ ] **Step 1: Add a shared test fixture module**

```python
from pathlib import Path

import pytest

from minx_mcp.db import get_connection
from minx_mcp.vault_writer import VaultWriter


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "minx.db"
    get_connection(path).close()
    return path


@pytest.fixture
def vault_writer(tmp_path: Path) -> VaultWriter:
    return VaultWriter(tmp_path / "vault", ("Minx",))
```

Also move the duplicated `_seed_event()` helper into `tests/conftest.py` so the review/core-server/goal-progress tests share one event seeding path.

- [ ] **Step 2: Update the existing tests to import the shared helpers instead of duplicating them**

```python
def test_daily_review_tool_returns_structured_result(
    db_path: Path,
    tmp_path: Path,
) -> None:
    conn = get_connection(db_path)
    seed_review_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()
```

- [ ] **Step 3: Refresh the README with the new Slice 2 capabilities and optional LLM config**

```md
## What works

- The Core MCP server exposes `daily_review`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, and `goal_archive`.
- Goal-aware daily review works without any LLM configured.
- Optional OpenAI-compatible review enrichment can be configured through the `core/llm_config` preference with an API key read from an environment variable.
```

- [ ] **Step 4: Update the roadmap doc after full verification is green**

```md
## Slice 2: Goals + Deeper Detection

**Status:** Implemented

**Implementation notes:**
- Goals are exposed through `minx-core` tools rather than a harness-specific conversational layer.
- OpenAI-compatible enrichment is optional; detector-only fallback remains supported.
- Hermes/Discord goal capture is intentionally deferred to a thin follow-on integration.
```

- [ ] **Step 5: Run the touched test files to verify the fixture extraction did not regress behavior**

Run: `.venv/bin/python -m pytest tests/test_core_server.py tests/test_review.py tests/test_goal_progress.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_core_server.py tests/test_review.py tests/test_goal_progress.py README.md docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md
git commit -m "docs: finalize slice 2 goal workflow docs"
```

## Task 10: Run Full Verification

**Files:**
- Modify: none

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: `PASS` with all existing and new tests green.

- [ ] **Step 2: Run mypy**

Run: `.venv/bin/python -m mypy`
Expected: `Success: no issues found ...`

- [ ] **Step 3: Manually smoke the Core MCP tool surface from Python**

Run:

```bash
.venv/bin/python - <<'PY'
import asyncio
from pathlib import Path
from minx_mcp.core.server import create_core_server

class _Config:
    db_path = Path("tmp-smoke/minx.db")
    vault_path = Path("tmp-smoke/vault")

server = create_core_server(_Config())
daily_review = server._tool_manager.get_tool("daily_review").fn
goal_create = server._tool_manager.get_tool("goal_create").fn
goal_list = server._tool_manager.get_tool("goal_list").fn

async def main():
    print(await daily_review("2026-03-15", False))

asyncio.run(main())
print(goal_create(
    title="Dining out under $250",
    goal_type="spending_cap",
    metric_type="sum_below",
    target_value=25000,
    period="monthly",
    domain="finance",
    category_names=["Dining Out"],
    merchant_names=[],
    account_names=[],
    starts_on="2026-03-01",
    ends_on=None,
    notes="smoke",
))
print(goal_list(status="active"))
PY
```

Expected:

- `daily_review` returns a success envelope with `data`
- `goal_create` returns a success envelope with a created goal
- `goal_list` returns that created goal

- [ ] **Step 4: Commit the final verified slice**

```bash
git add .
git commit -m "feat: implement slice 2 goals and drift"
```

## Self-Review

- Spec coverage: the plan covers the repo-contained Slice 2 requirements from `docs/superpowers/specs/2026-04-07-slice2-goals-drift-design.md`: async stabilization, OpenAI-compatible enrichment, goals CRUD, goal progress, goal-aware review, goal drift detectors, and docs refresh.
- Placeholder scan: there are no `TODO`/`TBD` markers; every task includes concrete files, tests, code snippets, and verification commands.
- Type consistency: the same goal concepts are used throughout the plan: `GoalCreateInput`, `GoalUpdateInput`, `GoalRecord`, `GoalProgress`, `GoalService`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, `goal_archive`, `detect_goal_drift`, and `detect_category_drift`.
