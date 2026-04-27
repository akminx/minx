# minx-mcp

A personal Life OS built as a set of MCP servers. Domain MCPs own facts (Finance, Meals, and Training). Minx Core owns interpretation — it consumes domain events, runs deterministic detectors, and exposes structured snapshots, historical signals, and goal trajectories for any MCP-capable harness to consume.

**Architecture:** Domains emit events → Core builds read models → Detectors generate signals → Harness consumes structured data and owns narrative, coaching, and scheduling. See [architecture design](docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md) for the full picture.

**Current state:** Slices 1–4, Slice 6a–6l, and Core-side Slice 8 are implemented. Finance/Meals/Training domains are online. Core exposes structured snapshots, insight history, goal trajectories, vault/wiki primitives, durable memory CRUD/reconciliation/search/graph edges, recoverable enrichment queue sweeps, OpenRouter-backed queued memory embeddings when configured, FTS-backed hybrid search fallback, snapshot archives, secret-gated memory/vault writes, and playbook audit tools.

**Next implementation focus:** Run CI parity and slow MCP smoke checks, then start Slice 9 Agentic Investigations on top of the completed Slice 6 retrieval/enrichment foundation.

**Hermes cutover status (2026-04-14):**
- Legacy MCPs (`financehub`, `souschef`) disabled in Hermes config for rollback-safe migration.
- Minx MCP endpoints are active for `minx_finance`, `minx_core`, `minx_meals`, and `minx_training`.
- Finance MCP canonical local port is `8000`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Verify

```bash
uv run ruff check minx_mcp tests scripts
uv run mypy minx_mcp
uv run pytest tests/ -x -q
```

## Run finance over stdio

```bash
.venv/bin/python -m minx_mcp.finance --transport stdio
```

## Run finance over HTTP

```bash
.venv/bin/python -m minx_mcp.finance --transport http --host 127.0.0.1 --port 8000
```

## Run core over stdio

```bash
.venv/bin/python -m minx_mcp.core --transport stdio
```

## Run core over HTTP

```bash
.venv/bin/python -m minx_mcp.core --transport http --host 127.0.0.1 --port 8001
```

## Run meals over HTTP

```bash
.venv/bin/python -m minx_mcp.meals --transport http --host 127.0.0.1 --port 8002
```

## Run training over HTTP

```bash
.venv/bin/python -m minx_mcp.training --transport http --host 127.0.0.1 --port 8003
```

## Start full stack for Hermes harness

```bash
./scripts/start_hermes_stack.sh
```

This launches:
- `minx-finance` on `http://127.0.0.1:8000`
- `minx-core` on `http://127.0.0.1:8001`
- `minx-meals` on `http://127.0.0.1:8002`
- `minx-training` on `http://127.0.0.1:8003`

## Run Slice 4 cross-domain smoke flow

```bash
.venv/bin/python scripts/hermes_slice4_smoke.py --db-path ~/.minx/data/minx.db --review-date 2026-04-13
```

This seeds a small meals+training scenario and prints the combined snapshot payload (including `cross.training_nutrition_mismatch` when conditions are met). By default the script runs against a temporary copy of the database so your source DB is not modified.

To intentionally write seed data directly into the provided DB:

```bash
.venv/bin/python scripts/hermes_slice4_smoke.py --db-path ~/.minx/data/minx.db --review-date 2026-04-13 --in-place
```

## Default local paths

- Database: `~/.minx/data/minx.db`
- Vault root: `~/Documents/minx-vault`
- Import staging root: `~/.minx/data/imports`

You can override these with:

- `MINX_DATA_DIR`
- `MINX_DB_PATH`
- `MINX_VAULT_PATH`
- `MINX_STAGING_PATH`
- `MINX_HTTP_HOST`
- `MINX_HTTP_PORT`
- `MINX_DEFAULT_TRANSPORT`

## What works

- Finance imports are restricted to the configured staging/import root.
- Finance stores money internally as integer cents and renders dollars at the MCP/report boundary.
- Weekly and monthly finance reports are generated with explicit lifecycle state in SQLite.
- The Core snapshot pipeline is implemented and covered by tests. `get_daily_snapshot` returns structured read models, detector signals, and attention items, and surfaces detector persistence problems as an inline `persistence_warning` instead of failing the whole call.
- The Core MCP server exposes `get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`, `vault_replace_section`, `vault_replace_frontmatter`, `vault_scan`, `vault_reconcile_memories`, `goal_parse`, goal CRUD, memory CRUD/search/graph/embedding tools, enrichment queue tools, pending memory review, and playbook audit/history tools.
- `goal_get` returns both the stored goal DTO and derived progress for an optional `review_date`; progress is `null` outside the goal lifetime.
- Goal progress `summary` text is human-facing convenience copy, not a strict downstream machine contract; clients should rely on structured fields like `status`, `actual_value`, `target_value`, and the current window instead of parsing summary wording.
- `goal_list()` defaults to active goals, while `goal_list(status=...)` can query other lifecycle states explicitly.
- Goal progress clamps to the goal lifetime, goal updates can intentionally clear `ends_on` and `notes`, and category drift is based on a real equal-length prior baseline instead of goal status alone.
- Goal drift/category drift work for category-, merchant-, and account-scoped finance goals, and non-`normal` events are excluded from the review timeline/output path.
- `goal_parse` supports both natural-language parsing and a structured-input validation path.
- `finance_query` supports both natural-language interpretation and a structured `intent` + `filters` path.
- A real stdio MCP smoke test now exists at [tests/test_core_mcp_stdio.py](tests/test_core_mcp_stdio.py).
- New memory/vault writes are scanned locally for secret-shaped values before persistence or external embedding; run `python scripts/scan_memory_for_secrets.py` once against existing databases after pulling Slice 6h.

## LLM config

Set the `core/llm_config` preference to a payload like:

```json
{
  "provider": "openai_compatible",
  "base_url": "https://openrouter.ai/api/v1",
  "model": "nvidia/nemotron-3-super-120b-a12b",
  "api_key_env": "OPENROUTER_API_KEY",
  "provider_preferences": {
    "only": ["deepinfra"],
    "quantizations": ["bf16"],
    "allow_fallbacks": false,
    "require_parameters": true
  }
}
```

`api_key_env` must point to an environment variable; API keys are not stored in preferences. `provider_preferences` is optional and is forwarded verbatim to the OpenAI-compatible provider request body, which is useful for OpenRouter provider routing.

## Memory Embeddings

Memory embeddings are offline-safe by default. `memory_hybrid_search` always works through SQLite FTS5; it reranks FTS candidates with stored embeddings only when OpenRouter is configured and compatible candidate embeddings exist.

Set `MINX_OPENROUTER_API_KEY` to enable `memory_embedding_enqueue` and `enrichment_sweep` processing for `memory.embedding` jobs. Optional knobs are `MINX_EMBEDDING_MODEL` (default `openai/text-embedding-3-small`), `MINX_EMBEDDING_DIMENSIONS`, `MINX_EMBEDDING_REQUEST_TIMEOUT_S`, and `MINX_EMBEDDING_MAX_COST_MICROUSD`. API keys are read from the environment only and are not returned in MCP responses.

For existing databases, run `python -m scripts.rebuild_memory_fts` after pulling Slice 6i and `python -m scripts.backfill_memory_fingerprints` for rows that pre-date Slice 6g fingerprints.

## Known limitations

- This is still a local single-user tool. There is no auth, multi-user coordination, or remote durability story beyond local SQLite and the filesystem.
- Report generation is recoverable and tracked, but it is not globally atomic across SQLite and the vault filesystem.
- Core detector persistence is local SQLite durability only. `get_daily_snapshot` remains useful when persistence fails, but historical queries may lag behind the latest snapshot until persistence succeeds.
