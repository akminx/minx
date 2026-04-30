# Operations

Reference material for running and maintaining Minx. For end-to-end setup and smokes, see [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Default paths

| Purpose | Default | Override |
|---|---|---|
| Database | `~/.minx/data/minx.db` | `MINX_DB_PATH` |
| Vault root | `~/Documents/minx-vault` | `MINX_VAULT_PATH` |
| Import staging root | `~/.minx/staging` | `MINX_STAGING_PATH` |
| Data dir (parent) | `~/.minx/data` | `MINX_DATA_DIR` |

## Environment variables

| Variable | Purpose |
|---|---|
| `MINX_DATA_DIR` | Base local data directory |
| `MINX_DB_PATH` | SQLite database path |
| `MINX_VAULT_PATH` | Obsidian-style vault root |
| `MINX_STAGING_PATH` | Allowed import staging root |
| `MINX_LITEPARSE_BIN` | LiteParse executable path/name for PDF text extraction (default `lit`) |
| `MINX_HTTP_HOST` | Default HTTP host (default `127.0.0.1`) |
| `MINX_HTTP_PORT` | Default HTTP port |
| `MINX_FINANCE_PORT` | Finance HTTP port used by the launcher (default `8000`) |
| `MINX_CORE_PORT` | Core HTTP port used by the launcher (default `8001`) |
| `MINX_MEALS_PORT` | Meals HTTP port used by the launcher (default `8002`) |
| `MINX_TRAINING_PORT` | Training HTTP port used by the launcher (default `8003`) |
| `MINX_DEFAULT_TRANSPORT` | `stdio` or `http` |
| `MINX_VAULT_SCAN_ON_SNAPSHOT` | Optional boolean to scan the vault while building snapshots (default `false`) |
| `MINX_OPENROUTER_API_KEY` | Enables memory embedding jobs |
| `MINX_EMBEDDING_MODEL` | Embedding model (default `openai/text-embedding-3-small`) |
| `MINX_EMBEDDING_DIMENSIONS` | Optional embedding dimensions truncation |
| `MINX_EMBEDDING_REQUEST_TIMEOUT_S` | Embedding request timeout |
| `MINX_EMBEDDING_MAX_COST_MICROUSD` | Per-sweep embedding cost ceiling |
| `MINX_MAX_TOOL_CALLS_PER_INVESTIGATION` | Soft Core-side cap on investigation steps (default 1000) |
| `OPENROUTER_API_KEY` | Used by the LLM tool-calling adapter (set whatever `api_key_env` the preference points to) |
| `MINX_INVESTIGATION_MODEL` | minx-hermes runner model override (documented here for stack setup; read by minx-hermes) |
| `MINX_INVESTIGATION_BASE_URL` | minx-hermes runner OpenAI-compatible base URL (documented here for stack setup; read by minx-hermes) |

## Default HTTP ports

| Server | Port |
|---|---|
| Finance | 8000 |
| Core | 8001 |
| Meals | 8002 |
| Training | 8003 |

## Running servers

Stdio (single server, typical for local LLM clients):

```bash
uv run python -m minx_mcp.core --transport stdio
```

HTTP (typical for Hermes):

```bash
uv run python -m minx_mcp.finance  --transport http --host 127.0.0.1 --port 8000
uv run python -m minx_mcp.core     --transport http --host 127.0.0.1 --port 8001
uv run python -m minx_mcp.meals    --transport http --host 127.0.0.1 --port 8002
uv run python -m minx_mcp.training --transport http --host 127.0.0.1 --port 8003
```

Or all four at once:

```bash
./scripts/start_hermes_stack.sh
```

## LLM preference shape

Stored in the `core/llm_config` preference. API keys must stay in environment variables; only the env-var *name* lives in the preference. The model id is operational configuration, not architecture. Current setup examples use Gemini 2.5 Flash on OpenRouter:

```json
{
  "provider": "openai_compatible",
  "base_url": "https://openrouter.ai/api/v1",
  "model": "google/gemini-2.5-flash",
  "api_key_env": "OPENROUTER_API_KEY",
  "timeout_seconds": 90.0,
  "provider_preferences": {
    "data_collection": "deny",
    "require_parameters": true,
    "allow_fallbacks": true
  },
  "reasoning": {"effort": "medium"}
}
```

Write this with `uv run scripts/configure-openrouter.py --model google/gemini-2.5-flash` (idempotent; re-run any time to change the model or routing). The minx-hermes runner can also be pointed at the same model with `MINX_INVESTIGATION_MODEL=google/gemini-2.5-flash`.

## Maintenance commands

Run these once for databases that predate the relevant migrations or behavior changes.

| Command | Purpose |
|---|---|
| `uv run python -m scripts.rebuild_finance_dedupe` | Rebuilds finance transaction fingerprints after dedupe algorithm changes |
| `uv run python -m scripts.backfill_memory_fingerprints` | Populates `content_fingerprint` for older memory rows |
| `uv run python -m scripts.rebuild_memory_fts` | Rebuilds FTS5 memory search for historical rows |
| `uv run python scripts/scan_memory_for_secrets.py` | Reports historical memory rows containing secret-shaped values |

## Smoke flow (cross-domain helper)

```bash
uv run python scripts/hermes_slice4_smoke.py \
  --db-path ~/.minx/data/minx.db --review-date 2026-04-13
```

By default the script operates on a temporary database copy. Add `--in-place` only when intentionally writing seed data to the provided database.

## Verification before merging or handing off

```bash
uv run ruff check minx_mcp tests scripts
uv run mypy minx_mcp
uv run pytest tests/ -q
```

Strict mypy on source; broad pytest coverage across domain services, migrations, MCP tools, transport smoke, and historical regressions. Add `-x` during local edit loops when you want pytest to stop at the first failure.

## Operational invariants

- Finance imports stay inside the configured staging root.
- Money is stored as integer cents.
- Memory capture defaults to candidate status; requires `memory_confirm` before becoming active.
- Memory embeddings exist only for active, unexpired memories.
- Investigation steps are digest-only; raw tool output is never persisted in Core.
- Secret-shaped values are blocked or redacted before any memory / vault / embedding write.
- Render template IDs are append-only contracts — change semantics by minting a new ID, never by changing an existing one.
