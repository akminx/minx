# minx-mcp

Shared Minx MCP platform with a finance domain and a daily review pipeline.

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

## Run core (daily review) over stdio

```bash
.venv/bin/python -m minx_mcp.core --transport stdio
```

## Run core over HTTP

```bash
.venv/bin/python -m minx_mcp.core --transport http --host 127.0.0.1 --port 8001
```

## Default local paths

- Database: `~/.minx/data/minx.db`
- Vault root: `~/Documents/minx-vault`
- Import staging root: `~/.minx/staging`

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
- The daily review pipeline is implemented and covered by tests. If persisting detector insights to SQLite or writing the vault note fails after the in-memory review is built, `generate_daily_review` raises `ReviewDurabilityError` with the `DailyReview` on `exc.artifact` and per-sink failures on `exc.failures` (LLM timeouts/errors still fall back to the detector-only narrative and do not trigger this).
- The Core MCP server exposes `daily_review`, `goal_capture`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, and `goal_archive`.
- `daily_review` returns a protected client-facing projection with explicit redaction metadata rather than the raw internal review artifact.
- `goal_get` returns both the stored goal DTO and derived progress for an optional `review_date`; progress is `null` outside the goal lifetime.
- `goal_list()` defaults to active goals, while `goal_list(status=...)` can query other lifecycle states explicitly.
- Goal progress clamps to the goal lifetime, goal updates can intentionally clear `ends_on` and `notes`, and category drift is based on a real equal-length prior baseline instead of goal status alone.
- Goal drift/category drift work for category-, merchant-, and account-scoped finance goals, and non-`normal` events are excluded from the review timeline/output path.
- Optional LLM enrichment can be loaded from the `core/llm_config` preference using the `openai_compatible` provider path.
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
- The daily review pipeline is also not globally atomic across SQLite and the vault filesystem. Both durability sinks are attempted before returning, so a failed detector write can still leave an updated vault note from the same run.
