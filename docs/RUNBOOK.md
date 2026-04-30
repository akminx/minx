# Minx Runbook

The single source of truth for bringing Minx + Hermes up end to end and running it against real data. If you (or an agent) only read one doc, read this one.

## Map of the system

```
┌─ Hermes harness (~/.hermes/) ───────────────────────────────────┐
│  /minx-investigate  /minx-plan  /minx-retro  /minx-onboard-entity│
│  └─> scripts/minx-investigate.py (in minx-hermes)                │
│       └─> hermes_loop:  Policy + Dispatcher + CoreClient + loop  │
│            ├──> Configured OpenAI-compatible model endpoint      │
│            └──> MCP servers ─┬─ minx-core    :8001               │
│                              ├─ minx-finance :8000               │
│                              ├─ minx-meals   :8002               │
│                              └─ minx-training:8003               │
│                                                                  │
│  Durable storage:  ~/.minx/data/minx.db   (SQLite)               │
│                    $MINX_VAULT_PATH       (Obsidian-style)       │
└──────────────────────────────────────────────────────────────────┘
```

Two repos, separate concerns:

| Repo | Path | Owns |
|---|---|---|
| `minx` | your `minx` checkout | The four MCP servers, durable storage, schema, all deterministic data and business logic |
| `minx-hermes` | your `minx-hermes` checkout | Hermes overlay: skills, scripts, the agentic loop, the production runner |

Hermes itself (the harness binary + config) lives at `~/.hermes/` and is upstream-managed.

## First-time setup (15 minutes)

### 1. Install minx

```bash
cd /path/to/minx
uv sync --all-extras
uv run pytest tests/ -q
```

If pytest fails before you've changed anything, stop and fix that — every other step depends on a clean baseline.

### 2. Create the database and vault

```bash
mkdir -p ~/.minx/data ~/.minx/staging
uv run python -c "from minx_mcp.config import get_settings; from minx_mcp.db import get_connection; get_connection(get_settings().db_path).close()"
```

The first connection applies all migrations. Verify:

```bash
sqlite3 ~/.minx/data/minx.db "SELECT name FROM _migrations ORDER BY name DESC LIMIT 5;"
# Expect 027_investigations.sql at or near the top.
```

If you don't have a vault yet:

```bash
mkdir -p ~/Documents/minx-vault/{Memory,Recipes,Investigations}
```

### 3. Configure OpenRouter (chat + embeddings)

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
export MINX_OPENROUTER_API_KEY=$OPENROUTER_API_KEY
export MINX_EMBEDDING_DIMENSIONS=512
uv run scripts/configure-openrouter.py --model google/gemini-2.5-flash
```

That writes the `core/llm_config` preference. `google/gemini-2.5-flash` is the recommended OpenRouter example for current smokes, but the model id is deployment configuration. Re-run with `--print` to see the resolved config without writing.

To change providers, models, or routing later, re-run with flags:

```bash
uv run scripts/configure-openrouter.py --model <openrouter-model-id> \
  --reasoning-effort medium
```

### 4. Start the four MCP servers

```bash
./scripts/start_hermes_stack.sh
```

This launches Core (8001), Finance (8000), Meals (8002), Training (8003) over HTTP. Confirm:

```bash
curl -s http://127.0.0.1:8001/mcp -o /dev/null -w "%{http_code}\n"   # 405 or 400 is fine — server is up
```

### 5. Smoke the LLM round-trip

Before loading any real data, prove the LLM + MCP path works:

```bash
cd /path/to/minx-hermes
export MINX_INVESTIGATION_MODEL=google/gemini-2.5-flash
uv run scripts/minx-investigate.py \
  --kind investigate \
  --question "smoke: just say hello and stop" \
  --max-tool-calls 1 --wall-clock-s 30
```

Expected: a JSON envelope with `"status": "succeeded"` and a one-sentence `answer_md`. If this fails, read [Troubleshooting](#troubleshooting) before going further.

## Real-data smokes (one domain at a time)

Don't load everything at once. Each domain has its own ingest path; isolating them tells you which ingest broke when something fails.

### Finance — start here, smallest blast radius

```bash
# 1. Drop ONE statement into staging
cp ~/Downloads/some-statement.csv ~/.minx/staging/

# 2. Preview the import (no DB writes)
uv run python - <<'PY'
import asyncio, json
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def go():
    async with streamablehttp_client("http://127.0.0.1:8000/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            result = await s.call_tool(
                "finance_import_preview",
                {
                    "source_ref": "~/.minx/staging/some-statement.csv",
                    "account_name": "DCU",
                },
            )
            print(json.dumps(result.structuredContent, indent=2))

asyncio.run(go())
PY

# 3. If preview looks right, run the import
#    (use whichever finance import tool the preview suggested)

# 4. Drive a real investigation
uv run scripts/minx-investigate.py --kind investigate \
  --question "what merchants did I spend the most at last month?" \
  --max-tool-calls 6 --wall-clock-s 60
```

What to check in the JSON output:

- `tool_call_count > 0` — the configured model actually picked tools.
- `citation_refs` non-empty — the answer is grounded.
- `status: succeeded` and `tool_call_count` well below the cap.
- `answer_md` cites concrete merchants/categories, not generic prose.

### Meals — once finance is clean

Sync your vault recipes first with `recipe_scan` / `recipes_reconcile` on the meals MCP, then:

```bash
uv run scripts/minx-investigate.py --kind plan \
  --question "weekly meal plan, 100g protein/day, using my pantry" \
  --max-tool-calls 8 --wall-clock-s 90
```

### Training — last (smallest data volume)

Log a few sessions via the training MCP, then:

```bash
uv run scripts/minx-investigate.py --kind retro \
  --question "what changed in my training this month?" \
  --max-tool-calls 6 --wall-clock-s 60
```

### Goal drift / memory context (after a few weeks of data)

```bash
uv run scripts/minx-investigate.py --kind investigate \
  --question "am I drifting on my dining-cap goal?" \
  --max-tool-calls 6 --wall-clock-s 60
```

### Force budget exhaustion (regression test for terminal-status guarantee)

```bash
uv run scripts/minx-investigate.py --kind investigate \
  --question "very ambiguous open-ended thing" \
  --max-tool-calls 2 --wall-clock-s 30
# Expect status=budget_exhausted, terminal row in Core.
```

## Live observability while running smokes

Run this in a second terminal while you're driving smokes — it's the cheapest debugging surface:

```bash
watch -n 2 "sqlite3 ~/.minx/data/minx.db 'SELECT id, kind, status, tool_call_count, json_extract(trajectory_json, \"\$[#-1].tool\") AS last_tool FROM investigations ORDER BY id DESC LIMIT 5;'"
```

If a run failed, get the error message:

```bash
sqlite3 ~/.minx/data/minx.db "SELECT id, status, error_message FROM investigations WHERE status IN ('failed','cancelled','budget_exhausted') ORDER BY id DESC LIMIT 5;"
```

Save runner output as a log per smoke so you can diff "good" vs "bad":

```bash
mkdir -p ~/.minx/smokes
cd /path/to/minx-hermes
uv run scripts/minx-investigate.py --kind investigate --question "..." 2>&1 \
  | tee ~/.minx/smokes/$(date +%Y%m%dT%H%M%S).json
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `LLMProviderError: Missing API key` | `OPENROUTER_API_KEY` not exported in the shell that launched the runner | `export OPENROUTER_API_KEY=...` and re-run |
| Runner returns 4xx; `tool_call_count == 0` | `data_collection: deny` returned no providers for your model | Run with `--data-collection allow` once to confirm; if so, pick a different model or relax the routing |
| `tool_call_count = max_tool_calls`, `status: budget_exhausted`, vague answer | Model is over-iterating; system prompt too loose | Bump `--max-tool-calls`, run with `--reasoning-effort high`, or narrow the question |
| Same `args_digest` appears on consecutive steps | Model is calling a tool repeatedly with the same args | Question is too broad; restrict context or add specificity |
| `Connection refused` on `127.0.0.1:8001` | MCP server isn't running | `./scripts/start_hermes_stack.sh` |
| `no MCP route configured for tool: X` | Model called a tool not in the allowlist | Either add it to `hermes_loop/runtime.py:DEFAULT_TOOL_ALLOWLIST` and `mcp_clients.py:_TOOL_ROUTING`, or tighten the system prompt |
| Investigation row stays `running` after the runner exits | Bug in the loop — should never happen | File it. The investigation smoke helpers live in the `minx-hermes` checkout. |
| Memory embeddings not rerun after backfill | Historical rows need FTS/fingerprint backfill before embedding, or the enrichment queue worker is not sweeping | Run `uv run python -m scripts.backfill_memory_fingerprints` and `uv run python -m scripts.rebuild_memory_fts`, then confirm the embedding/enrichment sweep is wired in your deployment |

When in doubt about a step or step shape, the canonical contract lives in `docs/superpowers/specs/2026-04-19-slice9-agentic-investigations.md` and `docs/superpowers/specs/2026-04-29-render-template-registry.md`.

## Verification before merging or handing off

```bash
# minx
cd /path/to/minx
uv run ruff check minx_mcp tests scripts
uv run mypy minx_mcp
uv run pytest tests/ -q

# minx-hermes
cd /path/to/minx-hermes
PYTHONPATH=$PWD uv run pytest tests/ -q
```

Use the full commands above before handoff. For a local edit loop, adding `-x` to pytest is fine when you want to stop at the first failure.

## What's next after smoke is boring

When you can run the four standard scenarios (finance, meals, training, drift) twice in a row without surprise, you've earned the right to:

1. Build the dashboard (read-only first; Core read APIs already exist).
2. Add eval scenarios as pytest cases that record-and-replay LLM responses, so model swaps don't silently regress.
3. Move toward Slice 7 (Ideas/Journal).

Anything before that point is premature.
