# Project Handoff

Status as of 2026-04-10: Slice 2.5 (MCP Surface Refactor) has been designed and specced. The spec has been reviewed and all issues resolved. The architecture doc and roadmap have been updated to reflect the MCP/harness responsibility split. The next step is to write the implementation plan and execute.

## Repo And Branch

- Repo: `/Users/akmini/Documents/minx-mcp`
- Branch: `main`
- Stack: Python 3.12, FastMCP, SQLite, Pydantic, pytest, OpenAI-compatible LLM path

## What Happened In This Session

A full architecture review led to a fundamental redesign of the MCP/harness boundary:

1. **Identified the core problem:** The MCP was doing harness work (narrative generation, markdown rendering, vault writing, LLM review evaluation) while missing what harnesses actually need (temporal signal history, goal trajectories, structured snapshots).

2. **Established the governing principle:** MCP owns data + deterministic signals + temporal history. Harness owns narrative, coaching, conversation, scheduling. If logic depends on user data/goals → MCP. If logic depends on how you talk to the user → harness.

3. **Designed Slice 2.5** to reshape the Core tool surface for harness consumption.

4. **Removed Slice 5** (Harness Adaptation) from the roadmap — the MCP protocol itself provides portability. Harnesses adapt through their own skill/plugin systems.

5. **Updated the architecture design doc** — replaced the "Harness Adaptation" and "Hermes-Like Agent Experience" sections with the "MCP / Harness Responsibility Split" section.

## Canonical Design Inputs

- **Slice 2.5 spec (PRIMARY — this is what to implement next):** [docs/superpowers/specs/2026-04-10-slice2.5-mcp-surface-refactor-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-10-slice2.5-mcp-surface-refactor-design.md)
- Architecture design (updated): [docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md)
- Roadmap slices (updated): [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md)
- Current domains hardening spec: [docs/superpowers/specs/2026-04-09-current-domains-hardening-and-finance-maturity-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-09-current-domains-hardening-and-finance-maturity-design.md)

## What Slice 2.5 Does

Reshapes the Core MCP tool surface so it serves as a structured data provider for smart harnesses (Hermes today, others tomorrow).

### New / Replaced Tools

| Tool | What It Does |
|---|---|
| `get_daily_snapshot` (replaces `daily_review`) | Returns structured read models + detector signals + attention items. Silently persists insights. No narrative, no vault write, no LLM. |
| `get_insight_history` (new) | Queries historical detector signals with recurrence counts. Enables "3rd week in a row" reasoning. |
| `get_goal_trajectory` (new) | Returns goal progress across recent periods with trend (improving/worsening/stable). |
| `persist_note` (new) | Generic vault write for harness-generated content. Harness generates narrative, MCP handles atomic write. |
| `goal_parse` (replaces `goal_capture`) | Parse-only with dual-path input (natural language or structured). Returns interpretation + ambiguities. Does not manage clarification turns. |
| `finance_query` (modified) | Dual-path: structured `intent`+`filters` or `natural_query`. Smart harness skips LLM. |

### What Gets Removed

- `review.py` → replaced by `snapshot.py` (no narrative, no vault write, no LLM review)
- `review_policy.py` → removed (protected review boundary no longer needed)
- `LLMInterface`, `LLMReviewResult`, `DailyReview` models → replaced by `DailySnapshot`, `SnapshotContext`
- `goal_capture.py` → renamed to `goal_parse.py`

### What Stays Unchanged

- All Finance MCP tools and server
- All detectors (spending spike, open loops, goal drift, category drift, goal risk)
- All read model builders
- Goal CRUD tools (create, list, get, update, archive)
- Event infrastructure
- Shared platform (db, contracts, audit, vault writer, jobs)
- All parsers and import workflows
- Schema migrations 001-008

## Hermes Integration Context

The user has a working Hermes agent setup at `~/.hermes/` with:
- OpenRouter as the model endpoint (primary: nemotron-3-super-120b)
- MCP servers: qmd, obsidian, souschef, financehub
- Minx skills: finance-import, finance-insights, finance-report, finance-budget, finance-goals, finance-recurring, finance-scrape, health-log, journal-scan, souschef, weekly-review
- Discord channels: home, finances, health, journal, reports, souschef
- Minx personality configured

After Slice 2.5 implementation, the user will add minx-core and minx-finance to the Hermes config and update skills to call minx-mcp tools instead of financehub. The Slice 2.5 spec has a Hermes Integration Reference appendix with the full migration map.

## Best Next Step

1. Use the Slice 2.5 spec to write an implementation plan (invoke the `writing-plans` skill)
2. Execute the plan — this is primarily a refactor of `core/server.py`, `core/review.py` → `core/snapshot.py`, and `core/models.py`, plus three new modules (`core/history.py`, `core/trajectory.py`, and updates to `core/goal_capture.py` → `core/goal_parse.py`)
3. Update tests
4. Verify full test suite passes
5. After implementation: wire minx-mcp into Hermes config and test with real data
