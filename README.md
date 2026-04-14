# minx-mcp

A personal Life OS built as a set of MCP servers. Domain MCPs own facts (Finance, Meals, and Training). Minx Core owns interpretation — it consumes domain events, runs deterministic detectors, and exposes structured snapshots, historical signals, and goal trajectories for any MCP-capable harness to consume.

**Architecture:** Domains emit events → Core builds read models → Detectors generate signals → Harness consumes structured data and owns narrative, coaching, and scheduling. See [architecture design](docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md) for the full picture.

**Current state:** Slices 1–4 implemented. Finance/Meals/Training domains are online. Core exposes `get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`, `goal_parse`, goal CRUD, and `finance_query` (all with dual-path structured + natural language input), plus nutrition/training-aware detectors and snapshot context.

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
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy
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
- The Core MCP server exposes `get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`, `goal_parse`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, and `goal_archive`.
- `goal_get` returns both the stored goal DTO and derived progress for an optional `review_date`; progress is `null` outside the goal lifetime.
- Goal progress `summary` text is human-facing convenience copy, not a strict downstream machine contract; clients should rely on structured fields like `status`, `actual_value`, `target_value`, and the current window instead of parsing summary wording.
- `goal_list()` defaults to active goals, while `goal_list(status=...)` can query other lifecycle states explicitly.
- Goal progress clamps to the goal lifetime, goal updates can intentionally clear `ends_on` and `notes`, and category drift is based on a real equal-length prior baseline instead of goal status alone.
- Goal drift/category drift work for category-, merchant-, and account-scoped finance goals, and non-`normal` events are excluded from the review timeline/output path.
- `goal_parse` supports both natural-language parsing and a structured-input validation path.
- `finance_query` supports both natural-language interpretation and a structured `intent` + `filters` path.
- A real stdio MCP smoke test now exists at [tests/test_core_mcp_stdio.py](/Users/akmini/Documents/minx-mcp/tests/test_core_mcp_stdio.py).

## LLM config

Set the `core/llm_config` preference to a payload like:

```json
{
  "provider": "openai_compatible",
  "base_url": "https://api.openai.com/v1",
  "model": "gpt-4o-mini",
  "api_key_env": "OPENAI_API_KEY"
}
```

`api_key_env` must point to an environment variable; API keys are not stored in preferences.

## Known limitations

- This is still a local single-user tool. There is no auth, multi-user coordination, or remote durability story beyond local SQLite and the filesystem.
- Report generation is recoverable and tracked, but it is not globally atomic across SQLite and the vault filesystem.
- Core detector persistence is local SQLite durability only. `get_daily_snapshot` remains useful when persistence fails, but historical queries may lag behind the latest snapshot until persistence succeeds.
