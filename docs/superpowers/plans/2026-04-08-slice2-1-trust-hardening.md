# Slice 2.1 Phase A Trust Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw `daily_review` MCP response with a coarse protected projection that does not expose raw timeline, spending, goal, or per-insight data by default.

**Architecture:** Keep internal review generation and vault persistence unchanged in `minx_mcp.core.review`, add a small deterministic projection layer that maps a full `DailyReview` artifact into a protected summary, and have `minx_mcp.core.server._daily_review()` return that protected summary at the MCP boundary. The protected view is allowlist-based, coarse, and explicitly tagged with redaction metadata.

**Tech Stack:** Python 3.12, dataclasses, pytest, existing Minx Core review pipeline, FastMCP contract wrappers

---

## File Structure

**Create**

- `minx_mcp/core/review_policy.py`
  Deterministic protected-review projection helpers and allowlist constants.

**Modify**

- `minx_mcp/core/models.py`
  Add dataclasses for the protected review shape if that improves type clarity.
- `minx_mcp/core/server.py`
  Return the protected projection instead of the raw artifact payload from `_daily_review()`.
- `tests/test_review.py`
  Add unit tests for protected projection behavior.
- `tests/test_core_server.py`
  Add MCP-boundary tests for the protected `daily_review` shape.

---

### Task 1: Add Protected Review Projection Types And Redaction Logic

**Files:**
- Create: `minx_mcp/core/review_policy.py`
- Modify: `minx_mcp/core/models.py`
- Test: `tests/test_review.py`

- [ ] **Step 1: Write the failing projection tests**

```python
from minx_mcp.core.models import DailyReview, DailyTimeline, GoalProgress, InsightCandidate, OpenLoopsSnapshot, SpendingSnapshot
from minx_mcp.core.review_policy import PROTECTED_ATTENTION_AREAS, build_protected_review


def test_build_protected_review_blocks_raw_structures_and_goal_text() -> None:
    artifact = DailyReview(
        date="2026-03-15",
        timeline=DailyTimeline(date="2026-03-15", entries=[]),
        spending=SpendingSnapshot(
            date="2026-03-15",
            total_spent_cents=12000,
            by_category={"Dining Out": 12000},
            top_merchants=[("Cafe", 12000)],
            vs_prior_week_pct=25.0,
            uncategorized_count=2,
            uncategorized_total_cents=4000,
        ),
        open_loops=OpenLoopsSnapshot(date="2026-03-15", loops=[]),
        goal_progress=[
            GoalProgress(
                goal_id=1,
                title="Find a new job",
                metric_type="count_below",
                target_value=1,
                actual_value=0,
                remaining_value=1,
                current_start="2026-03-01",
                current_end="2026-03-31",
                status="on_track",
                summary="Still private.",
                category_names=[],
                merchant_names=[],
                account_names=[],
            )
        ],
        insights=[
            InsightCandidate(
                insight_type="finance.spending_spike",
                dedupe_key="merchant:cafe:2026-03-15",
                summary="Spent more at Cafe than usual.",
                supporting_signals=["Cafe spending jumped 45%."],
                confidence=0.8,
                severity="warning",
                actionability="review",
                source="detector",
            )
        ],
        narrative="Spent $120.00 at Cafe and made progress on Find a new job.",
        next_day_focus=["Review Cafe spending", "Update Find a new job goal"],
        llm_enriched=False,
    )

    protected = build_protected_review(artifact)

    assert protected.date == "2026-03-15"
    assert protected.redaction_applied is True
    assert "timeline" in protected.blocked_fields
    assert "spending" in protected.blocked_fields
    assert "goal_progress" in protected.blocked_fields
    assert "insights" in protected.blocked_fields
    assert "markdown" in protected.blocked_fields
    assert "Find a new job" not in protected.narrative
    assert all(area in PROTECTED_ATTENTION_AREAS for area in protected.attention_areas)


def test_build_protected_review_coarsens_counts_into_buckets() -> None:
    protected = build_protected_review(_review_artifact_with_sensitive_details())

    assert protected.activity_level in {"none", "low", "moderate", "high"}
    assert protected.goal_attention_level in {"none", "some", "many"}
    assert protected.open_loop_level in {"none", "some", "many"}


@pytest.mark.parametrize(
    "forbidden",
    [
        "Find a new job",
        "Cafe",
        "Dining Out",
        "Checking",
        "$120.00",
        "finance.spending_spike",
    ],
)
def test_build_protected_review_removes_sensitive_narrative_tokens(forbidden: str) -> None:
    protected = build_protected_review(_review_artifact_with_sensitive_details())

    assert forbidden not in protected.narrative


def test_build_protected_review_removes_sensitive_focus_tokens() -> None:
    protected = build_protected_review(_review_artifact_with_sensitive_details())

    assert "Review Cafe spending" not in protected.next_day_focus
    assert "Update Find a new job goal" not in protected.next_day_focus


def test_build_protected_review_pins_attention_area_allowlist() -> None:
    protected = build_protected_review(_review_artifact_with_sensitive_details())

    assert all(area in PROTECTED_ATTENTION_AREAS for area in protected.attention_areas)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review.py::test_build_protected_review_blocks_raw_structures_and_goal_text tests/test_review.py::test_build_protected_review_coarsens_counts_into_buckets -q`
Expected: FAIL with `ModuleNotFoundError` for `minx_mcp.core.review_policy` or missing symbols.

- [ ] **Step 3: Add protected review dataclasses and projection helpers**

```python
@dataclass(frozen=True)
class ProtectedDailyReview:
    date: str
    llm_enriched: bool
    attention_areas: list[str]
    activity_level: str
    goal_attention_level: str
    open_loop_level: str
    narrative: str
    next_day_focus: list[str]
    redaction_applied: bool
    redaction_policy: str
    redacted_fields: list[str]
    blocked_fields: list[str]
```

```python
PROTECTED_ATTENTION_AREAS = ("activity", "goals", "open_loops", "spending")
REDACTION_POLICY = "core_default_v1"
ACTIVITY_LOW_MAX = 1
ACTIVITY_MODERATE_MAX = 3
MANY_THRESHOLD = 3


def build_protected_review(review: DailyReview) -> ProtectedDailyReview:
    activity_level = _bucket_activity(len(review.timeline.entries))
    goal_attention_level = _bucket_many_some_none(len(review.goal_progress))
    open_loop_level = _bucket_many_some_none(len(review.open_loops.loops))
    attention_areas = _build_attention_areas(review, activity_level, goal_attention_level, open_loop_level)
    narrative = _build_protected_narrative(activity_level, goal_attention_level, open_loop_level)
    next_day_focus = _build_protected_focus(goal_attention_level, open_loop_level, attention_areas)
    return ProtectedDailyReview(
        date=review.date,
        llm_enriched=review.llm_enriched,
        attention_areas=attention_areas,
        activity_level=activity_level,
        goal_attention_level=goal_attention_level,
        open_loop_level=open_loop_level,
        narrative=narrative,
        next_day_focus=next_day_focus,
        redaction_applied=True,
        redaction_policy=REDACTION_POLICY,
        redacted_fields=["narrative", "next_day_focus"],
        blocked_fields=["timeline", "spending", "goal_progress", "insights", "markdown"],
    )
```

```python
def _bucket_activity(entry_count: int) -> str:
    if entry_count <= 0:
        return "none"
    if entry_count <= ACTIVITY_LOW_MAX:
        return "low"
    if entry_count <= ACTIVITY_MODERATE_MAX:
        return "moderate"
    return "high"


def _bucket_many_some_none(count: int) -> str:
    if count <= 0:
        return "none"
    if count < MANY_THRESHOLD:
        return "some"
    return "many"


def _build_attention_areas(
    review: DailyReview,
    activity_level: str,
    goal_attention_level: str,
    open_loop_level: str,
) -> list[str]:
    areas: list[str] = []
    if activity_level != "none":
        areas.append("activity")
    if review.spending.total_spent_cents > 0 or review.spending.uncategorized_count > 0:
        areas.append("spending")
    if goal_attention_level != "none":
        areas.append("goals")
    if open_loop_level != "none":
        areas.append("open_loops")
    return areas
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_review.py::test_build_protected_review_blocks_raw_structures_and_goal_text tests/test_review.py::test_build_protected_review_coarsens_counts_into_buckets -q`
Expected: PASS

- [ ] **Step 5: Commit the projection layer**

```bash
git add minx_mcp/core/models.py minx_mcp/core/review_policy.py tests/test_review.py
git commit -m "feat: add protected daily review projection"
```

---

### Task 2: Return The Protected Projection At The MCP Boundary

**Files:**
- Modify: `minx_mcp/core/server.py`
- Test: `tests/test_core_server.py`

- [ ] **Step 1: Write the failing MCP boundary tests**

```python
@pytest.mark.asyncio
async def test_daily_review_tool_returns_protected_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T15:00:00Z",
        entity_ref="batch-1",
        source="tests",
        payload={
            "account_name": "Checking",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 3,
            "total_cents": -6000,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    result = await _daily_review(_TestConfig(db_path, tmp_path / "vault"), "2026-03-15", False)

    assert result["date"] == "2026-03-15"
    assert result["redaction_applied"] is True
    assert result["redaction_policy"] == "core_default_v1"
    assert "timeline" not in result
    assert "spending" not in result
    assert "goal_progress" not in result
    assert "insights" not in result
    assert "markdown" not in result
    assert isinstance(result["attention_areas"], list)
    assert result["activity_level"] in {"none", "low", "moderate", "high"}


@pytest.mark.asyncio
async def test_daily_review_contract_wraps_protected_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    daily_review = server._tool_manager.get_tool("daily_review").fn

    result = await daily_review("2026-03-15", False)

    assert result["success"] is True
    assert result["data"]["redaction_applied"] is True
    assert "blocked_fields" in result["data"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_core_server.py::test_daily_review_tool_returns_protected_projection tests/test_core_server.py::test_daily_review_contract_wraps_protected_projection -q`
Expected: FAIL because `_daily_review()` still returns raw timeline, spending, insights, and markdown fields.

- [ ] **Step 3: Wire the protected projection into `_daily_review()`**

```python
from dataclasses import asdict

from minx_mcp.core.review_policy import build_protected_review


async def _daily_review(
    config: CoreServiceConfig,
    review_date: str | None,
    force: bool,
) -> dict[str, object]:
    effective_date = _resolve_review_date(review_date)
    ctx = ReviewContext(
        db_path=config.db_path,
        finance_api=None,
        vault_writer=VaultWriter(config.vault_path, ("Minx",)),
        llm=None,
    )
    artifact = await generate_daily_review(effective_date, ctx, force=force)
    protected = build_protected_review(artifact)
    return asdict(protected)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_core_server.py::test_daily_review_tool_returns_protected_projection tests/test_core_server.py::test_daily_review_contract_wraps_protected_projection -q`
Expected: PASS

- [ ] **Step 5: Commit the MCP boundary change**

```bash
git add minx_mcp/core/server.py tests/test_core_server.py
git commit -m "feat: protect daily review mcp output"
```

---

### Task 3: Verify Internal Review Durability And Full Regression Coverage

**Files:**
- Modify: `tests/test_review.py`
- Modify: `tests/test_core_server.py`

- [ ] **Step 1: Add the regression test proving internal note persistence still uses the full review artifact**

```python
@pytest.mark.asyncio
async def test_generate_daily_review_still_writes_full_markdown_note(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_event(conn, occurred_at="2026-03-15T15:00:00Z")
    conn.commit()

    import minx_mcp.core.review as review

    monkeypatch.setattr(review, "create_llm", lambda config=None, db_path=None: None)

    artifact = await review.generate_daily_review(
        "2026-03-15",
        ReviewContext(
            db_path=db_path,
            finance_api=_finance_api_with_attention_items(),
            vault_writer=VaultWriter(tmp_path / "vault", ("Minx",)),
            llm=None,
        ),
    )

    protected = build_protected_review(artifact)

    assert artifact.narrative != protected.narrative
    note = (tmp_path / "vault" / "Minx" / "Reviews" / "2026-03-15-daily-review.md").read_text()
    assert artifact.narrative in note
```

- [ ] **Step 2: Run the targeted review and server tests**

Run: `.venv/bin/python -m pytest tests/test_review.py tests/test_core_server.py -q`
Expected: PASS

- [ ] **Step 3: Run the full test suite**

Run: `.venv/bin/python -m pytest tests -q`
Expected: PASS

- [ ] **Step 4: Run static typing**

Run: `.venv/bin/python -m mypy`
Expected: `Success: no issues found in 48 source files`

- [ ] **Step 5: Commit the verification-backed slice**

```bash
git add tests/test_review.py tests/test_core_server.py
git commit -m "test: cover protected daily review output"
```

---

## Spec Coverage Check

- Threat-model-aligned protected MCP boundary: covered by Tasks 1 and 2.
- Allowlist-based coarse output with blocked raw structures: covered by Tasks 1 and 2.
- Internal artifact and vault persistence unchanged: covered by Task 3.
- Pinned `attention_areas` allowlist and negative tests: covered by Task 1.

## Notes

- Keep `render_daily_review_markdown()` unchanged in this phase.
- Keep `generate_daily_review()` returning the full internal `DailyReview`.
- Avoid adding client negotiation or alternate trust levels in this slice.
- `quiet_day` was intentionally dropped from the protected response shape; clients derive quietness from `activity_level == "none"` instead.
- Using `server._tool_manager.get_tool(...).fn` in tests is acceptable in this repo because the existing Core server tests already use that pattern.
