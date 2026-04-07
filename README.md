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
- The daily review pipeline is implemented and covered by tests.

## Known limitations

- This is still a local single-user tool. There is no auth, multi-user coordination, or remote durability story beyond local SQLite and the filesystem.
- Report generation is recoverable and tracked, but it is not globally atomic across SQLite and the vault filesystem.
