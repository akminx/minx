# Slice 1: Event Pipeline + Daily Review

**Date:** 2026-04-06
**Status:** Drafted for review
**Scope:** Event contracts, Minx Core foundation, and the first daily review playbook
**Parent:** [Minx Life OS Architecture Design](2026-04-06-minx-life-os-architecture-design.md)
**Approach:** Hybrid intelligence (structured detectors + LLM evaluation pass)

## Goal

Deliver the first end-to-end cross-domain value: Finance MCP emits structured events, Minx Core builds read models and runs detectors, the review pipeline produces a DailyReview artifact, and any harness can trigger and render it. First harness is Hermes rendering to Discord + Obsidian vault.

## Success Criteria

This slice is successful when:

- Finance MCP emits validated events on meaningful state changes
- Minx Core builds read models from events and Finance read API
- Two detectors produce testable, deterministic insight candidates
- The review pipeline assembles a DailyReview artifact with LLM enrichment
- The pipeline degrades gracefully when the LLM is unavailable
- Hermes can trigger a review and receive a structured artifact to render
- A full markdown review note lands in the vault
- The review is idempotent (duplicate triggers do not produce duplicate data)

## Non-Goals

This slice does not attempt to:

- implement goals, goal tracking, or goal-based detection (slice 2)
- build category drift or goal drift detectors (slice 2)
- add a poll adapter for non-event sources (slice 5)
- support multiple domains (slices 3-4)
- implement memory promotion or durable memory (slice 6)
- add harness adaptation profiles (slice 5)
- add event sensitivity filtering or redaction (slice 2)
- build a dashboard or web UI (slice 9)

## Event Contract

### Event Table

A new shared platform migration adds the `events` table to `minx.db`. This is a shared platform table, like `jobs`, owned by the event infrastructure — not by any single domain.

```sql
CREATE TABLE events (
    id              INTEGER PRIMARY KEY,
    event_type      TEXT NOT NULL,
    domain          TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    entity_ref      TEXT,
    source          TEXT NOT NULL,
    payload         TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    sensitivity     TEXT NOT NULL DEFAULT 'normal'
);
CREATE INDEX idx_events_domain_type ON events(domain, event_type);
CREATE INDEX idx_events_occurred ON events(occurred_at);
```

The `sensitivity` column is included per the parent architecture contract. All slice 1 events emit with the default `'normal'`. Sensitivity-based filtering and redaction are deferred to slice 2, but the column exists from day one to avoid a migration.

### Event Publishing

A shared `emit_event()` function lives in `minx_mcp/core/events.py`. Domains import and call this function. They do not write to the `events` table directly.

```python
def emit_event(
    db: Connection,
    event_type: str,
    domain: str,
    occurred_at: str,
    entity_ref: str | None,
    source: str,
    payload: dict,
    schema_version: int = 1,
    sensitivity: str = "normal",
) -> int | None:
```

The `db` connection is the same connection the domain operation is using. When emission succeeds, the event is written in the same SQLite transaction as the domain operation and will commit or rollback with it. However, domain success does not guarantee event presence — if `emit_event()` encounters a validation or write failure, it logs the error and returns `None`, allowing the domain operation to proceed without the event.

### Event Querying

```python
@dataclass
class Event:
    id: int
    event_type: str
    domain: str
    occurred_at: str
    recorded_at: str
    entity_ref: str | None
    source: str
    payload: dict
    schema_version: int
    sensitivity: str

def query_events(
    db: Connection,
    domain: str | None = None,
    event_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    timezone: str | None = None,
) -> list[Event]:
```

When `timezone` is provided, `start` and `end` are interpreted as local dates (YYYY-MM-DD) and converted to UTC ranges for filtering against `occurred_at`. When `timezone` is `None`, `start` and `end` are treated as UTC timestamps. All filters are optional and composable.

### Event Payload Schemas

Each event type has a Pydantic model defining its payload shape. Validation happens at emit time inside `emit_event()`.

`emit_event()` is best-effort: if payload validation fails or the INSERT raises an unexpected error, the failure is logged and `emit_event()` returns `None` instead of an event ID. It never raises. The domain operation always succeeds regardless of event emission outcome. This means the event stream may have gaps, but domain operations are never disrupted by event infrastructure failures.

This is a deliberate tradeoff: event completeness is less important than domain reliability in slice 1. If event gaps become a problem, a stricter mode (where emission failure aborts the transaction) can be added later behind a config flag.

Slice 1 event types:

**`finance.transactions_imported`** (schema_version 1)

Note: the plural form is intentional — this event represents a batch import operation, not a single transaction. This diverges from the architecture doc's singular `finance.transaction_imported` example.

```python
class TransactionsImportedPayload(BaseModel):
    account_name: str
    account_id: int
    job_id: str              # TEXT UUID, matches platform jobs.id
    transaction_count: int
    total_cents: int
    source_kind: str
```

**`finance.transactions_categorized`** (schema_version 1)
```python
class TransactionsCategorizedPayload(BaseModel):
    count: int
    categories: list[str]
```

**`finance.report_generated`** (schema_version 1)
```python
class ReportGeneratedPayload(BaseModel):
    report_type: str        # 'weekly' or 'monthly'
    period_start: str
    period_end: str
    vault_path: str
```

**`finance.anomalies_detected`** (schema_version 1)
```python
class AnomaliesDetectedPayload(BaseModel):
    count: int
    total_cents: int
```

### Finance MCP Integration

The finance service layer calls `emit_event()` at these points:

- After successful transaction import (inside the existing savepoint)
- After successful bulk categorization
- After successful report generation
- After anomaly scan returns results

No changes to Finance MCP's external tool interface. Events are an internal side effect of existing operations.

## Finance Read API

Minx Core must not query Finance tables directly. Instead, Finance exposes a read API — a set of functions Core calls through a defined interface.

```python
# minx_mcp/finance/read_api.py

class FinanceReadAPI:
    """Read-only interface for Minx Core to query Finance domain data."""

    def get_spending_summary(
        self, start_date: str, end_date: str
    ) -> SpendingSummary:
        """Total spent, by-category breakdown, top merchants for a date range."""

    def get_uncategorized(
        self, start_date: str, end_date: str
    ) -> UncategorizedSummary:
        """Count and total of uncategorized transactions in a date range."""

    def get_failed_imports(self) -> list[FailedImport]:
        """Jobs with status='failed' from the platform `jobs` table where job_type='finance_import'.
        This queries the `jobs` table (which has a `status` column), not `finance_import_batches`."""

    def get_period_comparison(
        self, current_start: str, current_end: str,
        prior_start: str, prior_end: str,
    ) -> PeriodComparison:
        """Compare spending between two periods (totals, by-category deltas)."""
```

Return types are Pydantic models or dataclasses defined in `finance/read_api.py`. Core depends on these types, not on Finance's internal schema.

This API uses the same SQLite database and connection pattern as the existing service layer. It is not an MCP tool — it is an internal Python interface.

## Timezone Contract

All `occurred_at` timestamps in events are stored as UTC (ISO 8601 with `Z` suffix).

A `timezone` preference is stored in the existing `preferences` table:
- domain: `"core"`
- key: `"timezone"`
- value: IANA timezone string (e.g., `"America/New_York"`)

When building read models, the date parameter is interpreted in the user's configured timezone. Events are filtered by converting `occurred_at` to the local timezone and comparing against the requested date.

If no timezone preference is set, the system defaults to the machine's local timezone.

## Minx Core Package

### Structure

```
minx_mcp/core/
    __init__.py
    events.py           -- emit_event(), query_events(), payload models
    read_models.py      -- builds DailyTimeline, SpendingSnapshot, OpenLoopsSnapshot
    detectors.py        -- detector functions + InsightCandidate
    review.py           -- review pipeline orchestration
    models.py           -- DailyReview, shared dataclasses
    llm.py              -- LLMInterface protocol, factory, fallback logic
```

### Database Additions

New migration in `minx.db`:

```sql
CREATE TABLE insights (
    id                  INTEGER PRIMARY KEY,
    insight_type        TEXT NOT NULL,
    summary             TEXT NOT NULL,
    supporting_signals  TEXT NOT NULL,     -- JSON array
    confidence          REAL NOT NULL,
    severity            TEXT NOT NULL,     -- 'info', 'warning', 'alert'
    actionability       TEXT NOT NULL,     -- 'none', 'suggestion', 'action_needed'
    source              TEXT NOT NULL,     -- 'detector', 'llm', 'hybrid'
    review_date         TEXT NOT NULL,
    event_count         INTEGER NOT NULL,  -- lightweight fingerprint of input state
    expires_at          TEXT,
    created_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_insights_dedup
    ON insights(review_date, insight_type, summary);
```

The unique index on `(review_date, insight_type, summary)` provides idempotency. Duplicate insights from re-runs are silently skipped via `INSERT OR IGNORE`.

### What Minx Core Owns

- Event infrastructure (emit, query, payload schemas)
- Read model computation
- Detectors
- Review pipeline orchestration
- Insight persistence
- LLM interface and factory

### What Minx Core Does Not Own

- Finance domain logic (Finance MCP owns this)
- Vault writing (reuses existing `vault_writer.py`)
- LLM provider specifics (abstracted behind `LLMInterface`)
- Harness-specific rendering (harness owns this)

## Read Models

Read models are Python dataclasses, computed on-demand. They are not stored. Each builder is a pure function that takes a date and data sources (events + Finance read API) and returns a typed result.

### DailyTimeline

Ordered list of what happened on a given day.

```python
@dataclass
class TimelineEntry:
    occurred_at: str
    domain: str
    event_type: str
    summary: str
    entity_ref: str | None

@dataclass
class DailyTimeline:
    date: str
    entries: list[TimelineEntry]
```

Built from: events table, filtered by date in the user's timezone.

### SpendingSnapshot

Financial picture for the day and trailing period.

```python
@dataclass
class SpendingSnapshot:
    date: str
    total_spent_cents: int
    by_category: dict[str, int]
    top_merchants: list[tuple[str, int]]
    vs_prior_week_pct: float | None
    uncategorized_count: int
    uncategorized_total_cents: int
```

Built from: Finance read API (`get_spending_summary`, `get_uncategorized`, `get_period_comparison`).

`vs_prior_week_pct` is `None` when less than 2 weeks of data exist.

### OpenLoopsSnapshot

Things that need attention.

```python
@dataclass
class OpenLoop:
    domain: str
    loop_type: str
    description: str
    count: int | None
    severity: str

@dataclass
class OpenLoopsSnapshot:
    date: str
    loops: list[OpenLoop]
```

Built from: Finance read API (`get_uncategorized`, `get_failed_imports`). Failed/stale imports are sourced from the platform `jobs` table (which has `status` and `updated_at` columns), not from events. Slice 1 only defines success-side finance events — failure detection uses the existing job infrastructure.

## Detectors

Detectors are pure functions. Read model in, insight candidates out.

```python
@dataclass
class InsightCandidate:
    insight_type: str
    summary: str
    supporting_signals: list[str]
    confidence: float
    severity: str
    actionability: str
    source: str             # 'detector' for these
```

### Slice 1 Detectors

**`detect_spending_spike`**

Input: `SpendingSnapshot`
Fires when: `vs_prior_week_pct` exceeds threshold (default: 25%)
Cold start: returns empty list when `vs_prior_week_pct is None`
Output: one `InsightCandidate` with severity based on magnitude (+25% = warning, +50% = alert)
Breaks down by category if a single category drives >60% of the delta.

**`detect_open_loops`**

Input: `OpenLoopsSnapshot`
Fires when: any open loop exists
Cold start: always runs (open loops exist or they don't)
Output: one `InsightCandidate` per open loop, severity based on count and type (failed imports = warning, uncategorized = info unless count > 20)

### Detector Registry

A plain list in `detectors.py`:

```python
@dataclass
class ReadModels:
    timeline: DailyTimeline
    spending: SpendingSnapshot
    open_loops: OpenLoopsSnapshot

DetectorFn = Callable[[ReadModels], list[InsightCandidate]]

DETECTORS: list[DetectorFn] = [
    detect_spending_spike,
    detect_open_loops,
]
```

Each detector receives the full `ReadModels` bundle and uses only the fields it needs. This avoids a heterogeneous registry and makes the pipeline loop simple: `for detector in DETECTORS: insights.extend(detector(read_models))`.

No plugin system. Adding a detector means writing a function and appending to the list.

## LLM Interface

### Protocol

```python
class LLMInterface(Protocol):
    async def evaluate_review(
        self,
        timeline: DailyTimeline,
        spending: SpendingSnapshot,
        open_loops: OpenLoopsSnapshot,
        detector_insights: list[InsightCandidate],
    ) -> LLMReviewResult:
        ...

@dataclass
class LLMReviewResult:
    additional_insights: list[InsightCandidate]
    ranked_indices: list[int]           # indices into combined list: detector_insights + additional_insights
    narrative: str                       # summary in Minx's voice
    next_day_focus: list[str]           # 3-5 priorities
```

### Factory

`minx_mcp/core/llm.py` owns a factory function:

```python
def create_llm(config: dict | None = None) -> LLMInterface:
```

Config can be passed explicitly or read from preferences (`domain="core"`, `key="llm_config"`). The factory handles provider selection. Harnesses do not need to know about LLM details — they call `generate_daily_review()` and the factory handles the rest.

### Prompt Design

The LLM evaluation prompt receives:
- Read models formatted as structured text (not raw dicts)
- Detector-generated insights
- Instructions: rank insights by importance, add observations detectors missed, write a narrative summary in Minx's voice, suggest 3-5 priorities for tomorrow
- Output format: JSON matching `LLMReviewResult`

The prompt enforces structured output. If the LLM returns malformed JSON, the pipeline falls back to template mode.

## Review Pipeline

### Steps

```
trigger (harness calls generate_daily_review)
    |
    v
1. Check if forced re-run (force=True skips to step 2)
    v
2. Build read models
    |-- DailyTimeline from events
    |-- SpendingSnapshot from Finance read API
    |-- OpenLoopsSnapshot from Finance read API + events
    v
3. Run detectors --> list[InsightCandidate]
    v
4. LLM evaluation pass (if LLM available)
    |-- success --> merge additional insights, apply ranking, get narrative + focus
    |-- failure --> template fallback (see below)
    v
5. Assemble DailyReview artifact
    v
6. Persist insights to DB (INSERT OR IGNORE for idempotency)
    v
7. Write vault note (Minx/Reviews/YYYY-MM-DD-daily-review.md)
    v
8. Return DailyReview to caller
```

### Entry Point

```python
@dataclass
class ReviewContext:
    db_path: str                          # path to minx.db
    finance_api: FinanceReadAPI           # Finance domain read interface
    vault_writer: VaultWriter             # configured with allowed_roots=("Minx",)
    llm: LLMInterface | None = None      # None triggers factory lookup, then fallback

async def generate_daily_review(
    date: str,
    ctx: ReviewContext,
    force: bool = False,
) -> DailyReview:
```

The `ReviewContext` bundles all dependencies the pipeline needs. The harness constructs it once and passes it in. This follows the existing codebase pattern where `FinanceService` receives `db_path` and `VaultWriter` receives `vault_root` — no ambient global state.

If `ctx.llm` is `None`, the factory creates one from config. If the factory fails (no config, provider unavailable), the pipeline runs in fallback mode.

The `VaultWriter` in the context must be configured with `allowed_roots=("Minx",)`, separate from the Finance service's writer which uses `allowed_roots=("Finance",)`.

### DailyReview Artifact

```python
@dataclass
class DailyReview:
    date: str
    timeline: DailyTimeline
    spending: SpendingSnapshot
    open_loops: OpenLoopsSnapshot
    insights: list[InsightCandidate]
    narrative: str
    next_day_focus: list[str]
    llm_enriched: bool                  # whether LLM pass succeeded
```

This is what the harness receives. Hermes renders `narrative` + top insights as a Discord digest. The vault note contains the full review.

### Fallback Mode

When the LLM is unavailable or returns malformed output:

- `insights`: detector-generated only, ordered by severity then confidence
- `narrative`: template-generated from read models ("Today you spent $X across Y transactions. Top category: Z. N items need attention.")
- `next_day_focus`: derived from open loops ("Categorize N uncategorized transactions", "Check failed import for account X")
- `llm_enriched`: `False`

The fallback path is the reliability floor. It must be independently tested.

### Quiet Day Handling

When a review is triggered for a day with zero events and no open loops:

- Read models return empty/zero values
- Detectors return empty lists
- The pipeline produces a minimal DailyReview with a "quiet day" narrative
- The vault note is still written (maintains daily cadence)
- `next_day_focus` is empty or carries forward from prior day's open loops

### Idempotency

The review pipeline always regenerates the `DailyReview` artifact from source state (events + Finance read API). It does not cache or reconstruct the artifact from persisted insights — the `narrative`, `next_day_focus`, and `ranked_indices` are transient and not stored.

Idempotency is handled at the persistence layer, not at the pipeline entry:

1. **Insight persistence**: `INSERT OR IGNORE` on the unique index `(review_date, insight_type, summary)` prevents duplicate insight rows when the pipeline re-runs.
2. **Vault note**: `vault_writer.py` overwrites the file at the same path. Re-running produces the same file, not a duplicate.

By default, the pipeline runs unconditionally. The `force` parameter is reserved for future use (e.g., clearing existing insights before re-generating) but is accepted in the signature from day one for forward compatibility.

### Vault Note Format

Written to `Minx/Reviews/YYYY-MM-DD-daily-review.md`:

```markdown
# Daily Review — YYYY-MM-DD

## Summary
{narrative}

## Timeline
{formatted timeline entries}

## Spending
{spending snapshot formatted as table/list}

## Insights
{each insight with type, summary, severity, supporting signals}

## Open Loops
{open loops with descriptions and severity}

## Tomorrow's Focus
{next_day_focus as bullet list}

---
Generated: {timestamp} | LLM enriched: {yes/no}
```

## Testing Requirements

### Event Contract
- `emit_event()` validates payload against schema, rejects invalid payloads
- Events are written in the same transaction as domain operations
- `query_events()` filters correctly by domain, type, and date range with timezone

### Finance Integration
- Finance service emits correct events after import, categorize, report, anomaly scan
- Finance read API returns correct data for date ranges
- Events are not emitted when domain operations fail/rollback

### Read Models
- Each builder returns correct output for known input data
- Empty/zero cases handled (no transactions, no events)
- Timezone filtering works correctly at day boundaries

### Detectors
- `detect_spending_spike`: fires at threshold, silent below, correct severity scaling, cold-start returns empty
- `detect_open_loops`: one insight per loop, correct severity mapping, empty when no loops

### Review Pipeline
- End-to-end: events in -> DailyReview out with correct structure
- LLM fallback: review completes with template narrative when LLM unavailable
- Idempotency: duplicate trigger does not produce duplicate insights
- Quiet day: minimal review produced for days with no activity
- Vault note: written to correct path with correct format

### Fallback Path (Dedicated)
- Template narrative is coherent and includes key numbers from read models
- Next-day focus is derived correctly from open loops
- Output structure matches DailyReview exactly
- No LLM calls made in fallback mode

## Architectural Decisions and Constraints

### Single SQLite Database

All tables (platform, finance, core) live in one `minx.db`. This is acceptable for slice 1 with one domain. Write contention will become a concern when multiple domains are emitting events concurrently. The migration path (separate domain databases + shared events database) should be evaluated at slice 3.

### No Event Versioning Migration

Events carry `schema_version` from day one. When a payload shape changes in a future slice, consumers can branch on version. No migration of historical events is needed — old events retain their version, new events get the new version.

### Finance Read API Uses `amount_cents`

The Finance schema has both a legacy `amount REAL` column and the current `amount_cents INTEGER` column (added in migration 004). The Finance read API must use `amount_cents` exclusively for all aggregations and comparisons. The legacy `amount` column must not be referenced by any Minx Core code.

### First Async Boundary

`generate_daily_review` and `LLMInterface.evaluate_review` are `async`. This is the first async code in the codebase — the existing Finance MCP, services, and shared platform are all synchronous. FastMCP supports async tools, so there is no technical blocker. The LLM call is I/O-bound and benefits from async. Tests will need `pytest-asyncio` (already in dev dependencies). Read model builders and detectors remain synchronous — only the review orchestration and LLM interface are async.

### Detectors Are Intentionally Simple

Two detectors for slice 1. The LLM evaluation pass compensates for gaps in detector coverage. As data history grows and patterns become clear, new detectors are added in future slices. The LLM's role naturally shrinks to narration and edge cases over time.

## Future Slice Dependencies

This slice establishes infrastructure that later slices build on:

- **Slice 2** adds goals table, goal-based detectors, category drift detector, event sensitivity
- **Slice 3** (Meals MCP) reuses event contract, emit_event, read model pattern, detector registry
- **Slice 4** (Training MCP) same reuse pattern
- **Slice 5** adds poll adapter, harness adaptation profiles
- **Slice 6** adds memory promotion, durable memory retrieval in review pipeline

## Next Step

After this spec is approved, create an implementation plan decomposing the work into ordered tasks with dependencies.
