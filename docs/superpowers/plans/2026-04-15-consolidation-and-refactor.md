# Consolidation, Refactoring, and Observability Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution tracker:** `docs/superpowers/plans/consolidation.md`

**Goal:** Restructure the codebase for long-term maintainability, add observability for production debugging, and re-scope the roadmap to focus on what matters. This covers the consolidation, refactoring, and strategic items from the full project review.

**Prerequisite:** The code quality cleanup plan should be completed first (or in parallel where phases don't conflict), since some refactors here build on cleaned-up code.

---

## Phase 1: Structural Refactoring

### 1.1 Add `scoped_connection` context manager to `db.py`

The `conn = get_connection(...) / try / finally / conn.close()` pattern is repeated 10+ times across `core/server.py`, `snapshot.py`, `trajectory.py`, and `history.py`.

- Add to `minx_mcp/db.py`:
  ```python
  from contextlib import contextmanager

  @contextmanager
  def scoped_connection(db_path: Path):
      conn = get_connection(db_path)
      try:
          yield conn
      finally:
          conn.close()
  ```
- Replace all `conn = get_connection(...) / try / finally / conn.close()` blocks in `core/server.py` (at least 7 instances: `_goal_create`, `_goal_list`, `_goal_get`, `_goal_update`, `_goal_archive`, `_goal_parse`, plus snapshot/history handlers)
- Replace in `core/snapshot.py` `build_daily_snapshot`
- Replace in `core/trajectory.py` and `core/history.py`
- Run full test suite

### 1.2 Split `goal_parse.py` (1,073 lines → 5 focused modules)

This single file does 5 distinct jobs. Split into:


| New file                     | Responsibility                                                                                                                                                                                       | Approx lines |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| `goal_parse.py`              | Entry point: `parse_goal_input()` + router                                                                                                                                                           | ~70          |
| `goal_capture_nl.py`         | NL capture: `capture_goal_message()`, regex helpers, `_looks_like_create_message`, `_extract_subject_phrase`, `_resolve_subject`, `_resolve_create_period`, `_resolve_starts_on`, `_build_*_clarify` | ~400         |
| `goal_capture_llm.py`        | LLM-backed capture: `_capture_with_llm`, `_run_goal_capture_interpretation`, `_render_goal_capture_prompt`, `_build_llm_update_result`                                                               | ~150         |
| `goal_capture_structured.py` | Structured validation: `_validate_structured_goal_input`, `_validate_structured_create_payload`, `_validate_structured_update_payload`                                                               | ~250         |
| `goal_capture_utils.py`      | Shared text helpers: `_normalize_text`, `_compact_text`, `_contains_any_word`, `_build_create_payload`, `_summarize_goal_filters`                                                                    | ~150         |


Steps:

- Create `goal_capture_utils.py` — extract shared text helpers first (no import changes for other modules)
- Create `goal_capture_structured.py` — extract structured input validation
- Create `goal_capture_nl.py` — extract NL capture (imports from utils)
- Create `goal_capture_llm.py` — extract LLM capture (imports from utils and nl)
- Slim `goal_parse.py` to entry point only, importing from the 4 new modules
- Verify all imports are clean with `mypy`
- Run full test suite (especially `test_goal_parse.py`, `test_goal_capture.py`, `test_goal_parse_llm_fallback.py`)

### 1.3 Split `core/models.py` (513 lines → 5 focused modules)


| New file                  | Contents                                                                                                                |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `core/models.py`          | Read model dataclasses: Timeline, Spending, OpenLoops, Nutrition, Training, ReadModels, InsightCandidate                |
| `core/protocols.py`       | All Protocol interfaces: LLMInterface, FinanceReadInterface, MealsReadInterface, TrainingReadInterface, VaultWriterLike |
| `core/goal_models.py`     | GoalCreateInput, GoalUpdateInput, GoalRecord, GoalProgress, GoalCaptureResult, GoalCaptureOption                        |
| `core/query_models.py`    | FinanceQueryFilters, FinanceQueryPlan, FinanceQueryIntent, FinanceQueryClarificationType                                |
| `core/snapshot_models.py` | DailySnapshot, SnapshotContext, PersistenceWarning, DurabilitySinkFailure                                               |


Steps:

- Create `core/protocols.py` — extract all Protocol classes first
- Create `core/goal_models.py` — extract goal-related types
- Create `core/query_models.py` — extract finance query types
- Create `core/snapshot_models.py` — extract snapshot types
- Update all imports across the codebase (this will touch many files)
- Run `mypy` and full test suite

### 1.4 Extract `BaseService` for shared service patterns

`FinanceService`, `MealsService`, and `TrainingService` all repeat the same ~40 lines of thread-local connection management, `__enter__`/`__exit__`, and event emission.

- Create `minx_mcp/base_service.py`:
  ```python
  class BaseService:
      def __init__(self, db_path: Path) -> None:
          self._db_path = db_path
          self._local = threading.local()

      @property
      def db_path(self) -> Path:
          return self._db_path

      @property
      def conn(self) -> Connection:
          conn = getattr(self._local, "conn", None)
          if conn is None:
              conn = get_connection(self._db_path)
              self._local.conn = conn
          return conn

      def close(self) -> None:
          conn = getattr(self._local, "conn", None)
          if conn is not None:
              conn.close()
              self._local.conn = None

      def __enter__(self) -> Self:
          return self

      def __exit__(self, *exc: object) -> None:
          self.close()
  ```
- Make `FinanceService` inherit from `BaseService`, remove duplicated methods
- Make `MealsService` inherit from `BaseService`, remove duplicated methods
- Make `TrainingService` inherit from `BaseService`, remove duplicated methods
- Run full test suite

### 1.5 Extract shared validation helpers

Three modules define nearly identical date/input validation. Consolidate into one place.

- Create `minx_mcp/validation.py` with:
  - `validate_iso_date(value, *, field_name) -> date`
  - `validate_date_window(start, end) -> tuple[date, date]`
  - `require_non_empty(name, value) -> str`
  - `resolve_date_or_today(value, *, field_name) -> str`
  - `require_str(payload, key) -> str`
  - `require_int(payload, key) -> int`
  - `require_str_list(payload, key) -> list[str]`
  - `reject_unknown_keys(payload, allowed, *, context) -> None`
- Replace validators in `finance/server.py` (`_validate_date_window`, `_validate_date_range`, `_validate_iso_date`, `_require_non_empty`)
- Replace validators in `core/server.py` (`_resolve_review_date`)
- Replace validators in `goal_parse.py` / `goal_capture_structured.py` (`_require_str`, `_require_int`, etc.)
- Run tests

### 1.6 Fix the LLM duck-typing — use protocols properly

- Define `JSONLLMInterface` protocol in `core/protocols.py`:
  ```python
  class JSONLLMInterface(Protocol):
      async def run_json_prompt(self, prompt: str) -> str: ...
  ```
- Replace `object | None` with `JSONLLMInterface | None` for `llm` parameter in: `goal_parse.py`, `core/server.py` `_resolve_goal_capture_llm`, `finance/server.py`
- Remove `getattr(configured, "run_json_prompt", None)` duck-check in `core/server.py` — protocol handles this
- Run `mypy` to verify

### 1.7 Fix the `FinanceReadInterface` `Any` returns (circular import fix)

- Move data-only return types (`SpendingSummary`, `UncategorizedSummary`, `ImportJobIssue`, `PeriodComparison`, `IncomeSummary`) out of `finance/read_api.py`
- Place them in a new file (e.g., `finance/read_models.py` — note: check if this file already exists for something else) or into `finance/import_models.py`
- Both `core/protocols.py` and `finance/read_api.py` import from this shared location
- Replace `Any` return types in `FinanceReadInterface` with the real types
- Run `mypy`

### 1.8 Simplify the finance report pipeline

Currently 6 modules form a chain for generating markdown reports: `reports.py` (facade) → `report_orchestration.py` → `report_builders.py` → `report_models.py` → `report_rendering.py` → `report_persistence.py`. That's enterprise-level indirection for what produces a markdown file from SQLite queries.

- Merge `reports.py` (pure re-export facade) into callers — it adds indirection with no logic. Remove the file and update imports to point directly at the real modules
- Merge `report_persistence.py` into `report_orchestration.py` — persistence is 2 functions (`persist_report_run`, `upsert_report_run`) that are only called from orchestration. They don't need their own module
- Evaluate merging `report_rendering.py` into `report_builders.py` — rendering is tightly coupled to builder output shapes. If they change together, they should live together. Only keep them separate if templates grow significantly
- Keep `report_models.py` separate (data types shared across builders/rendering)
- Target state: 3 modules (`report_models.py`, `report_builders.py`, `report_orchestration.py`) instead of 6
- Update all imports and run tests

### 1.9 Harden event schema upcasting

`core/events.py` `_upcast_payload` iterates upcasters by version number and applies them sequentially. One misnumbered upcaster silently corrupts payloads. This matters more as you add new domains and event types.

- Add a registration-time check: when `UPCASTERS` is built, verify version keys are contiguous integers starting from 1 (no gaps, no duplicates). Raise at import time if violated
- Add a test that asserts every registered upcaster chain produces valid output when applied to a v1 payload (round-trip test per event type)
- Consider adding a `target_version` field to upcaster registration so the system knows what the final version should be, and can assert the chain reaches it
- Add a comment block at the top of the upcaster registry explaining the rules: versions must be sequential, each upcaster transforms from version N-1 to N, payloads are dicts
- Add a test that detects if someone adds a new event type without adding an upcaster entry (or explicitly opting out)

---

## Phase 2: Observability

### 2.1 Add structured logging

- Create `minx_mcp/logging_config.py`:
  ```python
  import json
  import logging
  import sys
  from datetime import datetime, timezone

  class JSONFormatter(logging.Formatter):
      def format(self, record: logging.LogRecord) -> str:
          payload = {
              "ts": datetime.now(timezone.utc).isoformat(),
              "level": record.levelname,
              "logger": record.name,
              "msg": record.getMessage(),
          }
          if record.exc_info and record.exc_info[0] is not None:
              payload["exc"] = self.formatException(record.exc_info)
          for key in ("tool", "duration_ms", "success", "error_code", "domain"):
              value = getattr(record, key, None)
              if value is not None:
                  payload[key] = value
          return json.dumps(payload)

  def configure_logging(*, level: str = "INFO") -> None:
      handler = logging.StreamHandler(sys.stderr)
      handler.setFormatter(JSONFormatter())
      root = logging.getLogger()
      root.handlers.clear()
      root.addHandler(handler)
      root.setLevel(getattr(logging, level.upper(), logging.INFO))
  ```
- Call `configure_logging()` in `core/__main__.py` before server starts
- Call `configure_logging()` in `finance/__main__.py`
- Call `configure_logging()` in `meals/__main__.py`
- Call `configure_logging()` in `training/__main__.py`
- Add a test that `configure_logging` sets up a JSON handler correctly

### 2.2 Add request-level logging to `wrap_tool_call`

- Modify `contracts.py` `wrap_tool_call` to accept `tool_name: str = ""` parameter and log: tool name, duration_ms, success, error_code
- Modify `contracts.py` `wrap_async_tool_call` similarly
- Update all tool registrations in `finance/server.py` to pass `tool_name=`
- Update all tool registrations in `core/server.py` to pass `tool_name=`
- Update all tool registrations in `meals/server.py` to pass `tool_name=`
- Update all tool registrations in `training/server.py` to pass `tool_name=`
- Add a test verifying log output on success and failure

### 2.3 Add health check resources

- Add a `health://status` MCP resource to `create_finance_server` returning `{"status": "ok", "server": "minx-finance"}`
- Same for `create_core_server`
- Same for `create_meals_server`
- Same for `create_training_server`
- Add a simple test that the resource is registered and returns valid JSON

---

## Phase 3: Tooling and CI

### 3.1 Add `ruff` linter

- Add `ruff` to dev dependencies in `pyproject.toml`
- Add `ruff` configuration to `pyproject.toml`:
  ```toml
  [tool.ruff]
  target-version = "py312"
  line-length = 100

  [tool.ruff.lint]
  select = ["E", "F", "I", "W", "UP", "B", "SIM"]
  ```
- Run `ruff check --fix minx_mcp tests` for the initial auto-fix pass
- Fix any remaining issues manually
- Add `ruff check minx_mcp tests` to CI (or pre-commit hook)

### 3.2 Add GitHub Actions CI pipeline

There's no automated test run on push or PR gate. You're relying on discipline to run checks locally.

- Create `.github/workflows/ci.yml`:
  ```yaml
  name: CI
  on:
    push:
      branches: [main]
    pull_request:
      branches: [main]

  jobs:
    check:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v4
        - uses: actions/setup-python@v5
          with:
            python-version: "3.12"
        - name: Install dependencies
          run: pip install -e ".[dev]"
        - name: Ruff
          run: ruff check minx_mcp tests
        - name: Mypy
          run: mypy minx_mcp
        - name: Tests
          run: pytest tests/ -x -q
  ```
- Verify the workflow runs on a push to a feature branch
- Add branch protection on `main` requiring CI to pass before merge (optional but recommended)

### 3.3 Verify `mypy` strictness

- Run `mypy minx_mcp` and catalog current errors
- Fix errors introduced by refactoring (especially protocol/type changes)
- Consider enabling stricter settings if the error count is manageable

---

## Phase 4: Testing and Migration Hardening

### 4.1 Add E2E test through actual MCP transport

All current tests call tool functions directly via `_tool_manager.get_tool`. Nothing tests the actual stdio/HTTP transport path that Hermes uses. A single smoke test that starts a server process and calls a tool over the real transport would catch serialization/transport bugs the current suite can't.

- Create `tests/test_transport_e2e.py`
- Write a test that starts one MCP server (e.g., `minx-finance`) as a subprocess using stdio transport
- Send a real MCP tool call (e.g., `safe_finance_summary`) over stdin and read the response from stdout
- Assert the response is valid JSON matching the expected `ToolResponse` shape
- Write a second test for the Core server (e.g., `get_daily_snapshot`)
- Mark these tests with `@pytest.mark.slow` or a custom marker so they can be skipped in fast local runs but always run in CI
- Add the marker to `pytest.ini` / `pyproject.toml` config

### 4.2 Add data migration strategy for non-additive schema changes

The current 12 migrations are all additive (CREATE TABLE). There's no plan for when a schema change needs to transform existing data (ALTER + backfill). This matters as you add memory tables and new domains.

- Document the migration contract in `minx_mcp/db.py` module docstring or a `docs/migrations.md`:
  - Migrations must be idempotent within a transaction (wrapped in BEGIN/COMMIT)
  - ALTER TABLE migrations must handle the case where the column already exists (use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` or check `pragma_table_info`)
  - Data backfill migrations should be separate from schema migrations (schema first, backfill second) so a failed backfill doesn't block schema
  - Every migration that modifies existing data must log what it changed (row counts)
- Add a helper to `db.py` for safe column-add:
  ```python
  def add_column_if_not_exists(conn: Connection, table: str, column: str, col_type: str) -> bool:
      existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
      if column in existing:
          return False
      conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
      return True
  ```
- Add a test that runs all migrations on a fresh DB, inserts sample data, then runs them again (idempotency proof)
- Add a test template / example for a data-transforming migration so future migrations have a pattern to follow

### 4.3 Acknowledge NL goal capture complexity (no action, known trade-off)

Even after splitting `goal_parse.py` into 5 modules, the NL parsing logic in `goal_capture_nl.py` is still ~400 lines of regex heuristics with many branches. This is inherent to NL parsing without a full LLM call — it's not slop, it's a hard problem. The LLM fallback path is the right long-term answer; the regex path is good enough for now but will accumulate edge cases over time.

- After Phase 1.2 (split), review `goal_capture_nl.py` and add a module-level docstring explaining the design trade-off: regex for speed/offline, LLM for accuracy, both paths converge on the same `GoalCaptureResult` shape
- Consider adding a `# Known limitations` section at the bottom of the module listing the edge cases the regex path doesn't handle (so future work targets the right problems instead of patching regex)
- Long-term: as LLM reliability improves, the regex path should shrink to a thin fallback, not grow

---

## Phase 5: Roadmap Re-Scoping

This is planning work, not code. Document decisions in `HANDOFF.md` or a new roadmap doc. Detailed spec docs for Slice 6 and 8 live at `docs/superpowers/specs/2026-04-15-slice6-durable-memory.md` and `docs/superpowers/specs/2026-04-15-slice8-proactive-autonomy.md`.

### 5.1 Kill or defer Slice 5 (Harness Adaptation)

- You have one harness (Hermes). You don't need a harness registry or behavior profiles
- If Hermes needs something specific, add it directly to Core
- Update roadmap doc to mark Slice 5 as deferred with rationale

### 5.2 Build Slice 6 (Durable Memory) — three-tier architecture

Slice 6 is fully designed (see spec doc). It uses a three-tier memory system:

- **Tier 1 (SQLite)**: Structured factual memory — preferences, patterns, entity facts
- **Tier 2 (SQLite)**: Episodic memory — snapshot archives for reproducibility
- **Tier 3 (Obsidian)**: LLM Wiki pattern — vault as a living knowledge surface, read by Core's vault scanner, written by Hermes via Core's persist tools

Key architecture decisions:

- Memory CRUD and detectors live in Core
- Wiki page generation/maintenance lives in the harness (follows the Slice 2.5 principle: prose and LLM output are harness concerns)
- Bidirectional vault sync: user edits memory notes in Obsidian, Core picks them up on next scan
- Karpathy's LLM Wiki pattern guides the vault layer design

Phases (8-11 days total):

- 6a: Memory schema + MemoryService + CRUD MCP tools + first detectors (2-3 days)
- 6b: Snapshot archive table + auto-persist (1-2 days)
- 6c: Vault scanner with frontmatter indexing (1-2 days)
- 6d: Inject MemoryContext into ReadModels and snapshot builder (1 day)
- 6e: Wiki page generation for memories — vault write-back (1 day)
- 6f: Bidirectional vault sync — user edits -> memory updates (1 day)
- 6g (optional): Semantic search with sqlite-vec (2-3 days, defer until needed)

### 5.3 Acknowledge Slice 7 (Journal MCP) is standard CRUD

- Follows the exact same pattern as Meals/Training
- Don't pre-plan; build when you want it
- No action needed now

### 5.4 Build Slice 8 (Proactive Autonomy) — Core + Harness split

Slice 8 is fully designed (see spec doc). Key decision: **scheduling and orchestration belong to the harness, not Core.** This follows the Slice 2.5 principle. Core provides:

- `playbook_runs` audit table for logging what the harness did
- `log_playbook_run` and `playbook_history` MCP tools
- `playbook://registry` MCP resource (read-only playbook manifest)
- Condition-checking tools (pending candidates, snapshot data, insight history)

Hermes provides:

- Cron scheduling (uses existing Hermes cron infrastructure)
- Playbook runner scripts that call Core MCP tools
- Confirmation conversation flows
- Wiki maintenance playbook (LLM Wiki pattern — generates/updates Obsidian pages)
- Notification decisions

First playbooks: Daily Review, Weekly Report, Wiki Update, Memory Review (with confirmation), Goal Nudge (with confirmation).

Phases:

- 8a (Core): Audit schema + log/history MCP tools + playbook registry resource (1.5 days)
- 8b (Core): Condition-checking tools (0.5 day)
- 8c (Hermes): Daily review + weekly report playbook scripts (2 days)
- 8d (Hermes): Wiki maintenance playbook (2-3 days)
- 8e (Hermes): Confirmation flow for memory candidates + risky actions (1-2 days)

### 5.5 Defer Slice 9 (Dashboard) — independent technology layer

- Completely separate from MCP architecture
- Build whenever you want a visual interface
- No dependencies on other slices

### 5.6 Update `HANDOFF.md` with revised priorities

- Document the new priority order:
  1. Consolidation + code quality (this plan + code quality plan)
  2. Observability (Phase 2 above)
  3. Slice 6: Durable Memory (full three-tier system)
  4. Slice 8: Proactive Autonomy (Core audit + Hermes playbooks)
  5. Slice 7: Journal MCP (when wanted)
- Update architectural decisions section with Core/Harness split for autonomy
- Reference spec docs for Slice 6 and 8

---

## Execution Order

Phases can partially overlap but have some dependencies:

```
Phase 1.1 (scoped_connection) ─── can start immediately
Phase 1.2 (split goal_parse)  ─── can start immediately, independent of 1.1
Phase 1.5 (validation helpers) ── depends on 1.2 completing (structured validators move)
Phase 1.3 (split models)      ─── can start after 1.2 (fewer merge conflicts)
Phase 1.4 (BaseService)       ─── can start independently
Phase 1.6 (LLM protocols)     ─── depends on 1.3 (protocols.py exists)
Phase 1.7 (Any returns)       ─── depends on 1.3
Phase 1.8 (report pipeline)   ─── can start independently
Phase 1.9 (event upcasting)   ─── can start independently

Phase 2 (observability)       ─── can start independently of Phase 1
Phase 3 (tooling + CI)        ─── can start independently; CI should go early
Phase 4 (testing + migration) ─── E2E test depends on Phase 2 (logging helps debug); migration strategy is independent
Phase 5 (roadmap)             ─── planning only, no code dependencies
```


| Phase                               | Effort            | Risk                                |
| ----------------------------------- | ----------------- | ----------------------------------- |
| 1. Structural refactoring (1.1–1.9) | 8–10 hours        | Medium (many files, imports shift)  |
| 2. Observability                    | 2–3 hours         | Low (additive, no breaking changes) |
| 3. Tooling + CI                     | 1 hour            | Low                                 |
| 4. Testing + migration hardening    | 2–3 hours         | Low                                 |
| 5. Roadmap                          | 1 hour (planning) | None                                |


**Total: ~16–19 hours of work.**

**Recommended session order:**

1. Session 1: Phase 3.1–3.2 (ruff + CI pipeline) + Phase 1.1 (scoped_connection) + Phase 2.1 (structured logging) — quick wins, ~2.5 hours
2. Session 2: Phase 1.2 (split goal_parse) — focused refactoring, ~2 hours
3. Session 3: Phase 1.4 (BaseService) + Phase 1.5 (validation) — extraction work, ~2 hours
4. Session 4: Phase 2.2 + 2.3 (request logging + health checks) + Phase 1.8 (report pipeline simplification) — observability + cleanup, ~3 hours
5. Session 5: Phase 1.3 (split models) + Phase 1.6–1.7 (protocols, types) — final structural, ~3 hours
6. Session 6: Phase 1.9 (event upcasting) + Phase 4.1 (E2E transport test) + Phase 4.2 (migration strategy) — hardening, ~3 hours
7. Any time: Phase 5 (roadmap decisions, can be done in 30 minutes of writing)

