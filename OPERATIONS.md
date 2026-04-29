# Operations

This guide covers local setup, verification, server startup, configuration, and one-time database maintenance for Minx MCP.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The project also works with `uv`:

```bash
uv sync --all-extras
```

## Verify

Run these before merging, handing off, or publishing a branch:

```bash
uv run ruff check minx_mcp tests scripts
uv run mypy minx_mcp
uv run pytest tests/ -x -q
```

## Run Servers

Run Core over stdio:

```bash
uv run python -m minx_mcp.core --transport stdio
```

Run individual servers over HTTP:

```bash
uv run python -m minx_mcp.finance --transport http --host 127.0.0.1 --port 8000
uv run python -m minx_mcp.core --transport http --host 127.0.0.1 --port 8001
uv run python -m minx_mcp.meals --transport http --host 127.0.0.1 --port 8002
uv run python -m minx_mcp.training --transport http --host 127.0.0.1 --port 8003
```

Start the full local stack:

```bash
./scripts/start_hermes_stack.sh
```

## Default Paths

| Purpose | Default |
| --- | --- |
| Database | `~/.minx/data/minx.db` |
| Vault root | `~/Documents/minx-vault` |
| Import staging root | `~/.minx/staging` |

## Configuration

Environment overrides:

| Variable | Purpose |
| --- | --- |
| `MINX_DATA_DIR` | Base local data directory |
| `MINX_DB_PATH` | SQLite database path |
| `MINX_VAULT_PATH` | Obsidian-style vault root |
| `MINX_STAGING_PATH` | Allowed import staging root |
| `MINX_HTTP_HOST` | Default HTTP host |
| `MINX_HTTP_PORT` | Default HTTP port |
| `MINX_DEFAULT_TRANSPORT` | `stdio` or `http` |
| `MINX_OPENROUTER_API_KEY` | Enables optional memory embedding jobs |
| `MINX_EMBEDDING_MODEL` | Embedding model override |
| `MINX_EMBEDDING_DIMENSIONS` | Optional embedding dimensions |
| `MINX_EMBEDDING_REQUEST_TIMEOUT_S` | Embedding request timeout |
| `MINX_EMBEDDING_MAX_COST_MICROUSD` | Per-sweep embedding cost ceiling |

## LLM Preference Example

Store provider config in the `core/llm_config` preference. API keys must stay in environment variables.

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

## Maintenance Commands

Run these once for databases that predate the relevant migrations or behavior changes:

```bash
python -m scripts.rebuild_finance_dedupe
python -m scripts.backfill_memory_fingerprints
python -m scripts.rebuild_memory_fts
python scripts/scan_memory_for_secrets.py
```

What they do:

| Command | Purpose |
| --- | --- |
| `rebuild_finance_dedupe` | Rebuilds finance transaction fingerprints after dedupe algorithm changes |
| `backfill_memory_fingerprints` | Populates `content_fingerprint` for older memory rows |
| `rebuild_memory_fts` | Rebuilds FTS5 memory search for historical rows |
| `scan_memory_for_secrets.py` | Reports historical memory rows containing secret-shaped values |

## Smoke Flow

Run the Slice 4 cross-domain smoke helper:

```bash
uv run python scripts/hermes_slice4_smoke.py --db-path ~/.minx/data/minx.db --review-date 2026-04-13
```

By default, the script operates on a temporary database copy. Add `--in-place` only when intentionally writing seed data to the provided database.

## Operational Invariants

- Finance imports must stay inside the configured staging root.
- Money is stored as integer cents.
- Memory capture defaults to candidate status and requires confirmation before becoming active.
- Memory embeddings should only exist for active, unexpired memories.
- Investigation steps stored by Core must be digest-only and must not contain raw tool output.
- Secret-shaped values must be blocked or redacted before memory, vault, or embedding persistence.
